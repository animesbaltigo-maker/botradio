from __future__ import annotations

from typing import Any


ACTION_SELECTOR_QUERY = "selector_query"
ACTION_SELECTOR_SLUG = "selector_slug"
ACTION_RANDOM = "random_track"

KIND_ANY = "ANY"
KIND_OP = "OP"
KIND_ED = "ED"

_KIND_TO_CODE = {
    KIND_ANY: "0",
    KIND_OP: "1",
    KIND_ED: "2",
}
_CODE_TO_KIND = {value: key for key, value in _KIND_TO_CODE.items()}


def make_query_action(query: str, kind_token: str) -> dict[str, Any]:
    return {
        "type": ACTION_SELECTOR_QUERY,
        "query": " ".join(query.split()).strip(),
        "kind_token": kind_token,
    }


def make_slug_action(slug: str, kind_token: str) -> dict[str, Any]:
    return {
        "type": ACTION_SELECTOR_SLUG,
        "slug": slug.strip().strip("/"),
        "kind_token": kind_token,
    }


def make_random_action(kind_token: str) -> dict[str, Any]:
    return {
        "type": ACTION_RANDOM,
        "kind_token": kind_token,
    }


def encode_start_parameter(action: dict[str, Any]) -> str | None:
    action_type = action.get("type")
    kind_code = _KIND_TO_CODE.get(str(action.get("kind_token") or KIND_ANY), "0")

    if action_type == ACTION_SELECTOR_SLUG:
        slug = str(action.get("slug") or "").strip()
        if not slug:
            return None
        return f"a{kind_code}_{slug}"

    if action_type == ACTION_RANDOM:
        return f"r{kind_code}"

    return None


def decode_start_parameter(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None

    cleaned = value.strip()
    if len(cleaned) >= 4 and cleaned[0] == "a" and cleaned[2] == "_":
        kind_token = _CODE_TO_KIND.get(cleaned[1], KIND_ANY)
        slug = cleaned[3:].strip()
        if slug:
            return make_slug_action(slug, kind_token)
        return None

    if len(cleaned) == 2 and cleaned[0] == "r":
        return make_random_action(_CODE_TO_KIND.get(cleaned[1], KIND_ANY))

    return None

