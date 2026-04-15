from __future__ import annotations

from dataclasses import dataclass
import random
import re
import unicodedata
from typing import Any

import httpx


DEFAULT_INCLUDE = "animethemes.animethemeentries.videos.audio,images,resources"


class AnimeThemesClientError(RuntimeError):
    pass


class AnimeNotFoundError(AnimeThemesClientError):
    pass


@dataclass(slots=True)
class ThemeTrack:
    anime_id: int
    anime_name: str
    anime_slug: str
    media_format: str | None
    season: str | None
    year: int | None
    image_link: str | None
    info_link: str | None
    theme_id: int
    theme_slug: str
    theme_type: str
    theme_sequence: int | None
    entry_id: int
    entry_version: int | None
    video_id: int
    audio_id: int | None
    episodes: str | None
    notes: str | None
    nsfw: bool
    spoiler: bool
    audio_link: str | None
    video_link: str | None
    source: str | None
    resolution: int | None
    tags: str | None

    @property
    def display_title(self) -> str:
        return f"{self.anime_name} - {self.display_theme}"

    @property
    def display_theme(self) -> str:
        label = self.theme_slug or f"{self.theme_type}{self.theme_sequence or ''}"
        if self.entry_version and self.entry_version > 1:
            return f"{label} v{self.entry_version}"
        return label


class AnimeThemesClient:
    def __init__(self, base_url: str, timeout: float = 25.0) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={
                "Accept": "application/json",
                "User-Agent": "AnimeThemesRadioBot/1.0",
            },
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def get_random_track(
        self,
        theme_type: str | None = None,
        *,
        exclude_video_id: int | None = None,
    ) -> ThemeTrack:
        for _ in range(12):
            payload = await self._get_json(
                "/anime",
                {
                    "sort": "random",
                    "page[size]": 1,
                    "filter[has]": "animethemes",
                    "include": DEFAULT_INCLUDE,
                },
            )
            anime = self._first_anime(payload)
            if not anime:
                continue
            tracks = self._extract_tracks(anime, theme_type)
            chosen = self._pick_track(tracks, exclude_video_id=exclude_video_id)
            if chosen:
                return chosen
        raise AnimeThemesClientError("Nao consegui encontrar uma faixa tocavel agora.")

    async def get_track_for_query(
        self,
        query: str,
        theme_type: str | None = None,
        *,
        exclude_video_id: int | None = None,
    ) -> ThemeTrack:
        anime = await self.find_anime(query)
        tracks = self._extract_tracks(anime, theme_type)
        chosen = self._pick_track(tracks, exclude_video_id=exclude_video_id)
        if not chosen:
            raise AnimeThemesClientError("Esse anime existe, mas nao achei temas compativeis com esse filtro.")
        return chosen

    async def get_track_for_slug(
        self,
        slug: str,
        theme_type: str | None = None,
        *,
        exclude_video_id: int | None = None,
    ) -> ThemeTrack:
        anime = await self.get_anime_by_slug(slug)
        tracks = self._extract_tracks(anime, theme_type)
        chosen = self._pick_track(tracks, exclude_video_id=exclude_video_id)
        if not chosen:
            raise AnimeThemesClientError("Nao achei outra faixa desse anime com esse filtro.")
        return chosen

    async def find_anime(self, query: str) -> dict[str, Any]:
        cleaned = " ".join(query.split())
        if not cleaned:
            raise AnimeNotFoundError("Informe um nome de anime para pesquisar.")

        anime = await self._find_first(
            "/anime",
            {
                "filter[name]": cleaned,
                "page[size]": 1,
                "include": DEFAULT_INCLUDE,
            },
        )
        if anime:
            return anime

        for slug_candidate in self._slug_variants(cleaned):
            anime = await self._find_first(
                "/anime",
                {
                    "filter[slug]": slug_candidate,
                    "page[size]": 1,
                    "include": DEFAULT_INCLUDE,
                },
            )
            if anime:
                return anime

        anime = await self._try_full_text_search(cleaned)
        if anime:
            return anime

        raise AnimeNotFoundError(
            f'Nao encontrei "{cleaned}" no AnimeThemes. Tente o titulo oficial do anime.'
        )

    async def get_anime_by_slug(self, slug: str) -> dict[str, Any]:
        cleaned_slug = slug.strip().strip("/")
        if not cleaned_slug:
            raise AnimeThemesClientError("Slug do anime invalido.")

        payload = await self._get_json(
            f"/anime/{cleaned_slug}",
            {"include": DEFAULT_INCLUDE},
        )
        anime = payload.get("anime")
        if isinstance(anime, dict):
            return anime

        anime = await self._find_first(
            "/anime",
            {
                "filter[slug]": cleaned_slug,
                "page[size]": 1,
                "include": DEFAULT_INCLUDE,
            },
        )
        if anime:
            return anime

        raise AnimeNotFoundError(f'Nao encontrei o anime "{cleaned_slug}".')

    async def _find_first(
        self,
        path: str,
        params: dict[str, Any],
    ) -> dict[str, Any] | None:
        payload = await self._get_json(path, params)
        return self._first_anime(payload)

    async def _try_full_text_search(self, query: str) -> dict[str, Any] | None:
        candidates = (
            ("/anime", {"filter[search]": query, "page[size]": 5, "include": DEFAULT_INCLUDE}),
            ("/api/anime", {"filter[search]": query, "page[size]": 5, "include": DEFAULT_INCLUDE}),
        )
        for path, params in candidates:
            try:
                payload = await self._get_json(path, params)
            except AnimeThemesClientError:
                continue
            anime = self._first_anime(payload)
            if anime:
                return anime
        return None

    async def _get_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._http.get(path, params=params)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = self._extract_error_message(exc.response)
            raise AnimeThemesClientError(detail or "A API do AnimeThemes respondeu com erro.") from exc
        except httpx.HTTPError as exc:
            raise AnimeThemesClientError(
                "Nao consegui falar com a API do AnimeThemes agora."
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise AnimeThemesClientError("A API do AnimeThemes devolveu um JSON invalido.") from exc

        if not isinstance(payload, dict):
            raise AnimeThemesClientError("A API do AnimeThemes devolveu um formato inesperado.")

        return payload

    def _extract_tracks(
        self,
        anime: dict[str, Any],
        theme_type: str | None = None,
    ) -> list[ThemeTrack]:
        tracks: list[ThemeTrack] = []
        image_link = self._pick_image_link(anime)
        info_link = self._pick_info_link(anime)
        for animetheme in anime.get("animethemes") or []:
            if theme_type and animetheme.get("type") != theme_type:
                continue
            for entry in animetheme.get("animethemeentries") or []:
                for video in entry.get("videos") or []:
                    audio = video.get("audio") or {}
                    audio_link = audio.get("link")
                    video_link = video.get("link")
                    if not audio_link and not video_link:
                        continue
                    tracks.append(
                        ThemeTrack(
                            anime_id=int(anime.get("id", 0)),
                            anime_name=str(anime.get("name") or "Anime desconhecido"),
                            anime_slug=str(anime.get("slug") or ""),
                            media_format=anime.get("media_format"),
                            season=anime.get("season"),
                            year=anime.get("year"),
                            image_link=image_link,
                            info_link=info_link,
                            theme_id=int(animetheme.get("id", 0)),
                            theme_slug=str(animetheme.get("slug") or ""),
                            theme_type=str(animetheme.get("type") or "??"),
                            theme_sequence=animetheme.get("sequence"),
                            entry_id=int(entry.get("id", 0)),
                            entry_version=entry.get("version"),
                            video_id=int(video.get("id", 0)),
                            audio_id=int(audio["id"]) if audio.get("id") is not None else None,
                            episodes=entry.get("episodes"),
                            notes=entry.get("notes"),
                            nsfw=bool(entry.get("nsfw")),
                            spoiler=bool(entry.get("spoiler")),
                            audio_link=audio_link,
                            video_link=video_link,
                            source=video.get("source"),
                            resolution=video.get("resolution"),
                            tags=video.get("tags"),
                        )
                    )
        return tracks

    @staticmethod
    def _pick_track(
        tracks: list[ThemeTrack],
        *,
        exclude_video_id: int | None = None,
    ) -> ThemeTrack | None:
        if not tracks:
            return None
        if exclude_video_id is None:
            return random.choice(tracks)
        filtered = [track for track in tracks if track.video_id != exclude_video_id]
        if filtered:
            return random.choice(filtered)
        return None

    @staticmethod
    def _pick_image_link(anime: dict[str, Any]) -> str | None:
        images = anime.get("images") or []
        if not isinstance(images, list):
            return None
        for facet in ("Large Cover", "Small Cover"):
            for image in images:
                if image.get("facet") == facet and image.get("link"):
                    return str(image["link"])
        for image in images:
            if image.get("link"):
                return str(image["link"])
        return None

    @staticmethod
    def _pick_info_link(anime: dict[str, Any]) -> str | None:
        resources = anime.get("resources") or []
        if not isinstance(resources, list):
            return None
        for site in ("aniDB", "MyAnimeList", "AniList"):
            for resource in resources:
                if resource.get("site") == site and resource.get("link"):
                    return str(resource["link"])
        for resource in resources:
            if resource.get("link"):
                return str(resource["link"])
        return None

    @staticmethod
    def _first_anime(payload: dict[str, Any]) -> dict[str, Any] | None:
        anime = payload.get("anime")
        if isinstance(anime, dict):
            return anime
        if isinstance(anime, list) and anime:
            first = anime[0]
            if isinstance(first, dict):
                return first
        return None

    @staticmethod
    def _extract_error_message(response: httpx.Response) -> str | None:
        try:
            payload = response.json()
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
        return None

    @staticmethod
    def _slug_variants(value: str) -> list[str]:
        normalized = (
            unicodedata.normalize("NFKD", value)
            .encode("ascii", "ignore")
            .decode("ascii")
            .lower()
        )
        tokens = re.findall(r"[a-z0-9]+", normalized)
        variants = {
            normalized.strip(),
            "".join(tokens),
            "-".join(tokens),
            "_".join(tokens),
        }
        return [variant for variant in variants if variant]
