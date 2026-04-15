from __future__ import annotations

from dataclasses import dataclass
import asyncio
import random
import re
import unicodedata
from typing import Any

import httpx

from services.cache import AsyncTTLCache
from services.errors import LocalizedError


DEFAULT_INCLUDE = "animethemes.song.artists,animethemes.animethemeentries.videos.audio,images,resources"
SEARCH_INCLUDE = "images,resources,animethemes"
SEARCH_PAGE_SIZE = 15
MAX_SEARCH_PAGES = 2
SEARCH_CACHE_TTL = 600.0
ANIME_CACHE_TTL = 1800.0
TRANSIENT_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


class AnimeThemesClientError(LocalizedError):
    pass


class AnimeNotFoundError(AnimeThemesClientError):
    pass


@dataclass(slots=True)
class AnimeCandidate:
    anime_id: int
    anime_name: str
    anime_slug: str
    media_format: str | None
    season: str | None
    year: int | None
    image_link: str | None
    info_link: str | None
    matching_theme_count: int
    total_theme_count: int


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
    song_title: str | None
    artist_names: tuple[str, ...]
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
            timeout=httpx.Timeout(timeout, connect=min(timeout, 10.0), pool=10.0),
            limits=httpx.Limits(max_connections=140, max_keepalive_connections=70),
            headers={
                "Accept": "application/json",
                "User-Agent": "RadioAnimesBot/2.0",
            },
        )
        self._search_cache: AsyncTTLCache[list[AnimeCandidate]] = AsyncTTLCache()
        self._anime_cache: AsyncTTLCache[dict[str, Any]] = AsyncTTLCache()

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
        raise AnimeThemesClientError("errors.no_playable_track")

    async def get_track_for_query(
        self,
        query: str,
        theme_type: str | None = None,
        *,
        exclude_video_id: int | None = None,
    ) -> ThemeTrack:
        matches = await self.search_anime(query, theme_type, limit=1)
        anime = await self.get_anime_by_slug(matches[0].anime_slug)
        tracks = self._extract_tracks(anime, theme_type)
        chosen = self._pick_track(tracks, exclude_video_id=exclude_video_id)
        if not chosen:
            raise AnimeThemesClientError("errors.no_match_for_filter")
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
            raise AnimeThemesClientError("errors.no_other_track")
        return chosen

    async def get_theme_choices_for_slug(
        self,
        slug: str,
        theme_type: str | None = None,
    ) -> list[ThemeTrack]:
        anime = await self.get_anime_by_slug(slug)
        tracks = self._extract_tracks(anime, theme_type)
        choices = self._collapse_theme_choices(tracks)
        if not choices:
            raise AnimeThemesClientError("errors.no_match_for_filter")
        return choices

    async def search_anime(
        self,
        query: str,
        theme_type: str | None = None,
        *,
        limit: int = 30,
    ) -> list[AnimeCandidate]:
        cleaned = " ".join(query.split())
        if not cleaned:
            raise AnimeNotFoundError("errors.anime_name_required")

        cache_key = f"{self._normalize_search_value(cleaned)}::{theme_type or 'ANY'}"

        async def loader() -> list[AnimeCandidate]:
            results: dict[str, AnimeCandidate] = {}
            for page in range(1, MAX_SEARCH_PAGES + 1):
                payload = await self._get_json(
                    "/anime",
                    {
                        "q": cleaned,
                        "page[size]": SEARCH_PAGE_SIZE,
                        "page[number]": page,
                        "include": SEARCH_INCLUDE,
                    },
                )
                anime_items = self._anime_items(payload)
                if not anime_items:
                    break

                for anime in anime_items:
                    candidate = self._to_anime_candidate(anime, theme_type)
                    if candidate:
                        results[candidate.anime_slug] = candidate

                if len(results) >= limit:
                    break
                if not self._has_next_page(payload):
                    break

            matches = sorted(
                results.values(),
                key=lambda candidate: (
                    -self._candidate_score(cleaned, candidate),
                    candidate.anime_name.lower(),
                    candidate.year or 0,
                ),
            )
            if not matches:
                raise AnimeNotFoundError("errors.anime_not_found", query=cleaned)
            return matches[: max(limit, SEARCH_PAGE_SIZE)]

        matches = await self._search_cache.get_or_create(
            cache_key,
            ttl_seconds=SEARCH_CACHE_TTL,
            loader=loader,
        )
        return matches[:limit]

    async def get_anime_by_slug(self, slug: str) -> dict[str, Any]:
        cleaned_slug = slug.strip().strip("/")
        if not cleaned_slug:
            raise AnimeThemesClientError("errors.invalid_slug")

        cache_key = cleaned_slug.lower()

        async def loader() -> dict[str, Any]:
            payload = await self._get_json(
                f"/anime/{cleaned_slug}",
                {"include": DEFAULT_INCLUDE},
                allow_not_found=True,
            )
            if payload:
                anime = payload.get("anime")
                if isinstance(anime, dict):
                    return anime

            fallback_payload = await self._get_json(
                "/anime",
                {
                    "filter[slug]": cleaned_slug,
                    "page[size]": 1,
                    "include": DEFAULT_INCLUDE,
                },
            )
            anime = self._first_anime(fallback_payload)
            if anime:
                return anime
            raise AnimeNotFoundError("errors.anime_not_found", query=cleaned_slug)

        return await self._anime_cache.get_or_create(
            cache_key,
            ttl_seconds=ANIME_CACHE_TTL,
            loader=loader,
        )

    async def _get_json(
        self,
        path: str,
        params: dict[str, Any],
        *,
        allow_not_found: bool = False,
    ) -> dict[str, Any] | None:
        for attempt in range(3):
            try:
                response = await self._http.get(path, params=params)
                if allow_not_found and response.status_code == 404:
                    return None
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                if status_code in TRANSIENT_STATUS_CODES and attempt < 2:
                    await asyncio.sleep(0.35 * (attempt + 1))
                    continue
                raise AnimeThemesClientError("errors.api_error") from exc
            except httpx.HTTPError as exc:
                if attempt < 2:
                    await asyncio.sleep(0.35 * (attempt + 1))
                    continue
                raise AnimeThemesClientError("errors.api_unreachable") from exc

            try:
                payload = response.json()
            except ValueError as exc:
                raise AnimeThemesClientError("errors.invalid_api_response") from exc

            if not isinstance(payload, dict):
                raise AnimeThemesClientError("errors.invalid_api_response")
            return payload

        raise AnimeThemesClientError("errors.api_unreachable")

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

            song = animetheme.get("song") or {}
            song_title = song.get("title")
            artist_names = self._extract_artist_names(song)

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
                            anime_name=str(anime.get("name") or "Anime"),
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
                            song_title=str(song_title).strip() if song_title else None,
                            artist_names=artist_names,
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

    def _collapse_theme_choices(self, tracks: list[ThemeTrack]) -> list[ThemeTrack]:
        grouped: dict[int, list[ThemeTrack]] = {}
        for track in tracks:
            grouped.setdefault(track.theme_id, []).append(track)

        choices = [
            sorted(group, key=self._theme_choice_sort_key)[0]
            for group in grouped.values()
        ]
        choices.sort(key=self._theme_display_sort_key)
        return choices

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

    def _to_anime_candidate(
        self,
        anime: dict[str, Any],
        theme_type: str | None,
    ) -> AnimeCandidate | None:
        animethemes = anime.get("animethemes") or []
        matching_theme_count = sum(
            1
            for animetheme in animethemes
            if not theme_type or animetheme.get("type") == theme_type
        )
        if theme_type and matching_theme_count == 0:
            return None
        return AnimeCandidate(
            anime_id=int(anime.get("id", 0)),
            anime_name=str(anime.get("name") or "Anime"),
            anime_slug=str(anime.get("slug") or ""),
            media_format=anime.get("media_format"),
            season=anime.get("season"),
            year=anime.get("year"),
            image_link=self._pick_image_link(anime),
            info_link=self._pick_info_link(anime),
            matching_theme_count=matching_theme_count,
            total_theme_count=len(animethemes),
        )

    def _candidate_score(self, query: str, candidate: AnimeCandidate) -> int:
        normalized_query = self._normalize_search_value(query)
        query_tokens = self._search_tokens(query)
        name = self._normalize_search_value(candidate.anime_name)
        slug = self._normalize_search_value(candidate.anime_slug.replace("_", " "))
        haystack = f"{name} {slug}".strip()

        score = 0
        if name == normalized_query:
            score += 500
        if slug == normalized_query:
            score += 450
        if name.startswith(normalized_query):
            score += 320
        if slug.startswith(normalized_query):
            score += 280
        if normalized_query and normalized_query in name:
            score += 180
        if normalized_query and normalized_query in slug:
            score += 160
        if query_tokens and all(token in name for token in query_tokens):
            score += 140
        if query_tokens and all(token in haystack for token in query_tokens):
            score += 120
        score += sum(18 for token in query_tokens if token in haystack)
        score += min(candidate.matching_theme_count, 40)
        return score

    @staticmethod
    def _theme_choice_sort_key(track: ThemeTrack) -> tuple[int, int, int, int, int, int]:
        return (
            0 if track.audio_link else 1,
            0 if track.entry_version in (None, 1) else 1,
            track.entry_version or 1,
            -(track.resolution or 0),
            -AnimeThemesClient._video_source_rank(track.source),
            track.video_id,
        )

    @staticmethod
    def _theme_display_sort_key(track: ThemeTrack) -> tuple[int, int, int, int, str]:
        base_slug = f"{track.theme_type}{track.theme_sequence or ''}".lower()
        slug = track.theme_slug.lower()
        type_rank = {"OP": 0, "ED": 1}.get(track.theme_type, 2)
        return (
            type_rank,
            0 if slug == base_slug else 1,
            0 if track.theme_sequence is not None else 1,
            track.theme_sequence or 9999,
            slug,
        )

    @staticmethod
    def _video_source_rank(source: str | None) -> int:
        ranks = {
            "WEB": 4,
            "BD": 3,
            "DVD": 2,
            "LD": 1,
            "RAW": 1,
            "VHS": 0,
        }
        return ranks.get((source or "").upper(), 0)

    @staticmethod
    def _pick_image_link(anime: dict[str, Any]) -> str | None:
        images = anime.get("images") or []
        if not isinstance(images, list):
            return None
        ranked: list[tuple[int, int, int, int, str]] = []
        for image in images:
            if not isinstance(image, dict):
                continue
            link = image.get("link")
            if not link:
                continue
            facet = str(image.get("facet") or "")
            width = int(image.get("width") or 0)
            height = int(image.get("height") or 0)
            resolution = int(image.get("resolution") or 0)
            ranked.append(
                (
                    AnimeThemesClient._image_facet_rank(facet),
                    width * height,
                    resolution,
                    max(width, height),
                    str(link),
                )
            )
        if not ranked:
            return None
        ranked.sort(reverse=True)
        return ranked[0][4]

    @staticmethod
    def _image_facet_rank(facet: str) -> int:
        normalized = re.sub(r"[^a-z]+", " ", (facet or "").lower()).strip()
        if not normalized:
            return 0
        priorities = {
            "large cover": 120,
            "cover": 110,
            "poster": 105,
            "large banner": 100,
            "banner": 95,
            "header": 90,
            "small cover": 80,
            "small banner": 70,
        }
        if normalized in priorities:
            return priorities[normalized]
        if "large" in normalized:
            return 75
        if "cover" in normalized:
            return 70
        if "banner" in normalized:
            return 65
        return 10

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
    def _anime_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
        anime = payload.get("anime")
        if isinstance(anime, list):
            return [item for item in anime if isinstance(item, dict)]
        if isinstance(anime, dict):
            return [anime]
        return []

    @staticmethod
    def _has_next_page(payload: dict[str, Any]) -> bool:
        links = payload.get("links")
        if not isinstance(links, dict):
            return False
        return bool(links.get("next"))

    @staticmethod
    def _extract_artist_names(song: dict[str, Any]) -> tuple[str, ...]:
        names: list[str] = []
        artists = song.get("artists") or []
        if isinstance(artists, list):
            for artist in artists:
                if not isinstance(artist, dict):
                    continue
                name = artist.get("name")
                if isinstance(name, str):
                    cleaned = name.strip()
                    if cleaned and cleaned not in names:
                        names.append(cleaned)
        return tuple(names)

    @staticmethod
    def _normalize_search_value(value: str) -> str:
        normalized = (
            unicodedata.normalize("NFKD", value)
            .encode("ascii", "ignore")
            .decode("ascii")
            .lower()
        )
        return " ".join(re.findall(r"[a-z0-9]+", normalized))

    @classmethod
    def _search_tokens(cls, value: str) -> list[str]:
        normalized = cls._normalize_search_value(value)
        return normalized.split()
