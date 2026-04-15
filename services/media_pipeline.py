from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
from urllib.parse import urlparse

import httpx

from services.animethemes_client import ThemeTrack
from services.errors import LocalizedError


DEFAULT_WINDOWS_FFMPEG_CANDIDATES = (
    Path(r"C:\Users\kayky\Downloads\ffmpeg-8.1-essentials_build\bin\ffmpeg.exe"),
    Path(r"C:\Users\kayky\Downloads\ffmpeg-8.1-essentials_build\ffmpeg-8.1-essentials_build\bin\ffmpeg.exe"),
)

TRANSIENT_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


@dataclass(slots=True)
class PreparedTrack:
    mp3_path: Path
    thumbnail_path: Path | None


class MediaPipeline:
    def __init__(
        self,
        cache_dir: str | Path,
        *,
        ffmpeg_path: str = "",
        timeout: float = 60.0,
    ) -> None:
        self._cache_dir = Path(cache_dir)
        self._audio_src_dir = self._cache_dir / "audio-src"
        self._audio_mp3_dir = self._cache_dir / "audio-mp3"
        self._image_src_dir = self._cache_dir / "image-src"
        self._thumb_dir = self._cache_dir / "thumbs"
        for path in (
            self._audio_src_dir,
            self._audio_mp3_dir,
            self._image_src_dir,
            self._thumb_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

        self._ffmpeg = self._resolve_ffmpeg(ffmpeg_path)
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=min(timeout, 10.0), pool=10.0),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=80, max_keepalive_connections=30),
            headers={"User-Agent": "RadioAnimesBot/2.0"},
        )
        self._download_semaphore = asyncio.Semaphore(12)
        self._convert_semaphore = asyncio.Semaphore(max(2, min(4, os.cpu_count() or 2)))
        self._prepare_lock = asyncio.Lock()
        self._prepare_tasks: dict[int, asyncio.Task[PreparedTrack]] = {}

    async def close(self) -> None:
        await self._http.aclose()

    async def prepare_track(self, track: ThemeTrack) -> PreparedTrack:
        if not track.audio_link:
            raise LocalizedError("errors.track_unavailable")

        key = int(track.audio_id or track.video_id or 0)
        existing = self._prepared_from_disk(track)
        if existing:
            return existing

        async with self._prepare_lock:
            existing = self._prepared_from_disk(track)
            if existing:
                return existing

            task = self._prepare_tasks.get(key)
            if task is None:
                task = asyncio.create_task(self._prepare_track_impl(track))
                self._prepare_tasks[key] = task
                creator = True
            else:
                creator = False

        try:
            return await task
        finally:
            if creator:
                async with self._prepare_lock:
                    if self._prepare_tasks.get(key) is task:
                        self._prepare_tasks.pop(key, None)

    async def _prepare_track_impl(self, track: ThemeTrack) -> PreparedTrack:
        source_ext = self._guess_extension(track.audio_link or "", default=".ogg")
        source_path = self._audio_src_dir / f"{track.audio_id or track.video_id}{source_ext}"
        mp3_path = self._audio_mp3_dir / f"{track.audio_id or track.video_id}.mp3"

        await self._download_if_missing(track.audio_link or "", source_path)

        thumbnail_path: Path | None = None
        image_source: Path | None = None
        if track.image_link:
            image_ext = self._guess_extension(track.image_link, default=".img")
            image_source = self._image_src_dir / f"{track.anime_id}{image_ext}"
            thumbnail_path = self._thumb_dir / f"{track.anime_id}.jpg"
            await self._download_if_missing(track.image_link, image_source)
            if not thumbnail_path.exists() or thumbnail_path.stat().st_size == 0:
                async with self._convert_semaphore:
                    await asyncio.to_thread(self._create_thumbnail, image_source, thumbnail_path)

        if not mp3_path.exists() or mp3_path.stat().st_size == 0:
            async with self._convert_semaphore:
                await asyncio.to_thread(self._convert_audio_to_mp3, source_path, mp3_path, image_source)

        return PreparedTrack(mp3_path=mp3_path, thumbnail_path=thumbnail_path)

    def _prepared_from_disk(self, track: ThemeTrack) -> PreparedTrack | None:
        mp3_path = self._audio_mp3_dir / f"{track.audio_id or track.video_id}.mp3"
        if not mp3_path.exists() or mp3_path.stat().st_size == 0:
            return None

        thumbnail_path = self._thumb_dir / f"{track.anime_id}.jpg"
        if not thumbnail_path.exists() or thumbnail_path.stat().st_size == 0:
            thumbnail_path = None
        return PreparedTrack(mp3_path=mp3_path, thumbnail_path=thumbnail_path)

    async def _download_if_missing(self, url: str, target_path: Path) -> None:
        if target_path.exists() and target_path.stat().st_size > 0:
            return
        if not url:
            raise LocalizedError("errors.media_download")

        temp_path = target_path.with_suffix(target_path.suffix + ".tmp")
        if temp_path.exists():
            temp_path.unlink()

        async with self._download_semaphore:
            for attempt in range(3):
                try:
                    async with self._http.stream("GET", url) as response:
                        response.raise_for_status()
                        with temp_path.open("wb") as file_obj:
                            async for chunk in response.aiter_bytes(65536):
                                file_obj.write(chunk)
                    temp_path.replace(target_path)
                    return
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in TRANSIENT_STATUS_CODES and attempt < 2:
                        await asyncio.sleep(0.35 * (attempt + 1))
                        continue
                    if temp_path.exists():
                        temp_path.unlink(missing_ok=True)
                    raise LocalizedError("errors.media_download") from exc
                except httpx.HTTPError as exc:
                    if attempt < 2:
                        await asyncio.sleep(0.35 * (attempt + 1))
                        continue
                    if temp_path.exists():
                        temp_path.unlink(missing_ok=True)
                    raise LocalizedError("errors.media_download") from exc

    def _convert_audio_to_mp3(
        self,
        source_path: Path,
        target_path: Path,
        cover_path: Path | None = None,
    ) -> None:
        temp_path = target_path.with_suffix(".tmp.mp3")
        if temp_path.exists():
            temp_path.unlink()

        if cover_path and cover_path.exists():
            command = [
                str(self._ffmpeg),
                "-y",
                "-i",
                str(source_path),
                "-i",
                str(cover_path),
                "-map",
                "0:a:0",
                "-map",
                "1:v:0",
                "-codec:a",
                "libmp3lame",
                "-b:a",
                "128k",
                "-codec:v",
                "mjpeg",
                "-disposition:v",
                "attached_pic",
                "-id3v2_version",
                "3",
                str(temp_path),
            ]
        else:
            command = [
                str(self._ffmpeg),
                "-y",
                "-i",
                str(source_path),
                "-vn",
                "-codec:a",
                "libmp3lame",
                "-b:a",
                "128k",
                str(temp_path),
            ]
        self._run_ffmpeg(command, "errors.audio_convert")
        temp_path.replace(target_path)

    def _create_thumbnail(self, source_path: Path, target_path: Path) -> None:
        temp_path = target_path.with_suffix(".tmp.jpg")
        if temp_path.exists():
            temp_path.unlink()
        command = [
            str(self._ffmpeg),
            "-y",
            "-i",
            str(source_path),
            "-frames:v",
            "1",
            "-vf",
            "scale=320:-2",
            "-q:v",
            "4",
            str(temp_path),
        ]
        self._run_ffmpeg(command, "errors.thumbnail_create")
        temp_path.replace(target_path)

    def _run_ffmpeg(self, command: list[str], error_key: str) -> None:
        try:
            subprocess.run(
                command,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise LocalizedError(error_key) from exc

    @staticmethod
    def _guess_extension(url: str, *, default: str) -> str:
        suffix = Path(urlparse(url).path).suffix.lower()
        if suffix and len(suffix) <= 5:
            return suffix
        return default

    @staticmethod
    def _resolve_ffmpeg(explicit_path: str) -> Path:
        candidates = []
        if explicit_path:
            candidates.append(Path(explicit_path))
        which_path = shutil.which("ffmpeg")
        if which_path:
            candidates.append(Path(which_path))
        candidates.extend(DEFAULT_WINDOWS_FFMPEG_CANDIDATES)

        for candidate in candidates:
            if candidate.exists():
                return candidate

        raise LocalizedError("errors.ffmpeg_missing")

