from __future__ import annotations

import re
from dataclasses import dataclass


STICKER_TAG = re.compile(r'<sticker\s+name=["\']([^"\']+)["\']\s*/?>', re.IGNORECASE)


@dataclass(frozen=True)
class ParsedModelResponse:
    text: str
    text_segments: list[str]
    sticker_category: str | None


def parse_model_response(text: str) -> ParsedModelResponse:
    categories = [value.strip() for value in STICKER_TAG.findall(text) if value.strip()]
    cleaned = STICKER_TAG.sub("", text).strip()
    segments = [value.strip() for value in cleaned.split("|||") if value.strip()]
    return ParsedModelResponse(
        text=cleaned,
        text_segments=segments,
        sticker_category=categories[0] if categories else None,
    )
