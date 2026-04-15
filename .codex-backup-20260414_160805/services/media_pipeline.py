from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
from urllib.parse import urlparse

import httpx

from services.animethemes_client import AnimeThemesClientError, ThemeTrack


DEFAULT_WINDOWS_FFMPEG_CANDIDATES = (
    Path(r"C:\Users\kayky\Downloads\ffmpeg-8.1-essentials_build\bin\ffmpeg.exe"),
    Path(r"C:\Users\kayky\Downloads\ffmpeg-8.1-essentials_build\ffmpeg-8.1-essentials_build\bin\ffmpeg.exe"),
)


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
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "AnimeThemesRadioBot/1.0"},
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def prepare_track(self, track: ThemeTrack) -> PreparedTrack:
        if not track.audio_link:
            raise AnimeThemesClientError("Esse tema nao tem audio disponivel.")

        source_ext = self._guess_extension(track.audio_link, default=".ogg")
        source_path = self._audio_src_dir / f"{track.audio_id or track.video_id}{source_ext}"
        mp3_path = self._audio_mp3_dir / f"{track.audio_id or track.video_id}.mp3"

        await self._download_if_missing(track.audio_link, source_path)

        thumbnail_path: Path | None = None
        image_source: Path | None = None
        if track.image_link:
            image_ext = self._guess_extension(track.image_link, default=".img")
            image_source = self._image_src_dir / f"{track.anime_id}{image_ext}"
            thumbnail_path = self._thumb_dir / f"{track.anime_id}.jpg"
            await self._download_if_missing(track.image_link, image_source)
            if not thumbnail_path.exists() or thumbnail_path.stat().st_size == 0:
                await asyncio.to_thread(self._create_thumbnail, image_source, thumbnail_path)

        if not mp3_path.exists() or mp3_path.stat().st_size == 0:
            await asyncio.to_thread(self._convert_audio_to_mp3, source_path, mp3_path, image_source)

        return PreparedTrack(mp3_path=mp3_path, thumbnail_path=thumbnail_path)

    async def _download_if_missing(self, url: str, target_path: Path) -> None:
        if target_path.exists() and target_path.stat().st_size > 0:
            return

        temp_path = target_path.with_suffix(target_path.suffix + ".tmp")
        if temp_path.exists():
            temp_path.unlink()

        try:
            async with self._http.stream("GET", url) as response:
                response.raise_for_status()
                with temp_path.open("wb") as file_obj:
                    async for chunk in response.aiter_bytes():
                        file_obj.write(chunk)
        except httpx.HTTPError as exc:
            raise AnimeThemesClientError("Nao consegui baixar a midia do AnimeThemes.") from exc

        temp_path.replace(target_path)

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
        self._run_ffmpeg(command, "converter o audio para mp3")
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
        self._run_ffmpeg(command, "gerar a thumbnail do audio")
        temp_path.replace(target_path)

    def _run_ffmpeg(self, command: list[str], action: str) -> None:
        try:
            subprocess.run(
                command,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise AnimeThemesClientError(f"Nao consegui {action}.") from exc

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

        raise AnimeThemesClientError(
            "FFmpeg nao foi encontrado. Configure FFMPEG_PATH ou instale o ffmpeg."
        )
