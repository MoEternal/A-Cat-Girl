from __future__ import annotations

import base64
import math
import re
from dataclasses import dataclass
from html import unescape
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from .token_counter import count_text_tokens


SUPPORTED_MIME_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_NORMALIZED_BYTES = 3584 * 1024
MAX_SOURCE_PIXELS = 64_000_000
MAX_IMAGE_PIXELS = 4_000_000
MAX_IMAGE_DIMENSION = 4096
MAX_IMAGES_PER_MESSAGE = 4
IMAGE_TOKEN_ESTIMATE = 1000

CQ_IMAGE_URL_PATTERN = re.compile(r"\[CQ:(?:image|mface),[^\]]*?url=([^,\]\s]+)")
CQ_CODE_PATTERN = re.compile(r"\[CQ:[^\]]+\]")
DATA_IMAGE_PATTERN = re.compile(r"data:image/[a-zA-Z0-9.+-]+;base64,", re.IGNORECASE)


class MediaValidationError(ValueError):
    pass


@dataclass(frozen=True)
class NormalizedImage:
    data: bytes
    mime_type: str
    width: int
    height: int
    original_bytes: int
    transformed: bool

    @property
    def base64_data(self) -> str:
        return base64.b64encode(self.data).decode("ascii")

    @property
    def data_uri(self) -> str:
        return f"data:{self.mime_type};base64,{self.base64_data}"


def parse_cq_message(raw_message: str) -> tuple[str, list[str]]:
    """Extract text and QQ image URLs without letting mface entities break CQ parsing."""
    raw_urls = CQ_IMAGE_URL_PATTERN.findall(raw_message)
    image_urls = [unescape(url) for url in raw_urls]
    text = unescape(CQ_CODE_PATTERN.sub("", raw_message)).strip()
    return text, image_urls


def sniff_image_mime(data: bytes, fallback: str | None = None) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if fallback in SUPPORTED_MIME_TYPES:
        return fallback
    raise MediaValidationError("Unsupported or unrecognized image format")


def _resize_to_limits(image: Image.Image) -> tuple[Image.Image, bool]:
    width, height = image.size
    source_pixels = width * height
    if source_pixels > MAX_SOURCE_PIXELS:
        raise MediaValidationError("Image pixel count exceeds the source safety limit")

    scale = min(
        1.0,
        MAX_IMAGE_DIMENSION / max(width, height),
        math.sqrt(MAX_IMAGE_PIXELS / source_pixels) if source_pixels else 1.0,
    )
    if scale >= 1.0:
        return image, False
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return image.resize(size, Image.Resampling.LANCZOS), True


def _as_rgb(image: Image.Image) -> Image.Image:
    if image.mode == "RGB":
        return image
    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(background, rgba).convert("RGB")
    return image.convert("RGB")


def _encode_jpeg(image: Image.Image, quality: int) -> bytes:
    output = BytesIO()
    _as_rgb(image).save(output, format="JPEG", quality=quality, optimize=True)
    return output.getvalue()


def normalize_image_bytes(
    data: bytes,
    declared_mime: str | None = None,
    max_bytes: int = MAX_NORMALIZED_BYTES,
) -> NormalizedImage:
    """Normalize one image before request-time base64 encoding.

    The returned bytes are never intended for chat-history persistence.
    """
    if not data:
        raise MediaValidationError("Image is empty")
    if len(data) > MAX_SOURCE_BYTES:
        raise MediaValidationError("Image download exceeds the source byte limit")

    mime_type = sniff_image_mime(data, declared_mime)
    try:
        with Image.open(BytesIO(data)) as opened:
            width, height = opened.size
            if width * height > MAX_SOURCE_PIXELS:
                raise MediaValidationError("Image pixel count exceeds the source safety limit")
            opened.seek(0)
            image = ImageOps.exif_transpose(opened).copy()
    except (UnidentifiedImageError, OSError) as exc:
        raise MediaValidationError("Image bytes could not be decoded") from exc

    image, resized = _resize_to_limits(image)
    transformed = resized

    if mime_type == "image/gif":
        output = BytesIO()
        image.convert("RGBA").save(output, format="PNG", optimize=True)
        normalized = output.getvalue()
        mime_type = "image/png"
        transformed = True
        if len(normalized) <= max_bytes:
            return NormalizedImage(
                normalized, mime_type, image.width, image.height, len(data), transformed
            )

    if not transformed and len(data) <= max_bytes:
        return NormalizedImage(data, mime_type, image.width, image.height, len(data), False)

    for quality in (85, 75, 65, 55, 45, 35):
        normalized = _encode_jpeg(image, quality)
        if len(normalized) <= max_bytes:
            return NormalizedImage(
                normalized, "image/jpeg", image.width, image.height, len(data), True
            )

    working = image
    for _ in range(8):
        size = (max(1, round(working.width * 0.8)), max(1, round(working.height * 0.8)))
        working = working.resize(size, Image.Resampling.LANCZOS)
        normalized = _encode_jpeg(working, 75)
        if len(normalized) <= max_bytes:
            return NormalizedImage(
                normalized, "image/jpeg", working.width, working.height, len(data), True
            )

    raise MediaValidationError("Image could not be normalized below the request byte limit")


def build_openai_image_part(image: NormalizedImage) -> dict:
    return {"type": "image_url", "image_url": {"url": image.data_uri}}


def build_multimodal_user_content(text: str, images: list[NormalizedImage]) -> list[dict] | str:
    if not images:
        return text or "（发了一条空消息）"
    if len(images) > MAX_IMAGES_PER_MESSAGE:
        raise MediaValidationError(
            f"A message may contain at most {MAX_IMAGES_PER_MESSAGE} images"
        )
    parts = [build_openai_image_part(image) for image in images]
    parts.append({"type": "text", "text": text or "（图片）"})
    return parts


def build_history_content(text: str, image_files: list[str | Path]) -> str:
    placeholders = [f"[图片{index}: {Path(path).name}]" for index, path in enumerate(image_files, 1)]
    pieces = placeholders + ([text] if text else ["（图片）"])
    content = "\n".join(pieces)
    ensure_history_content_safe(content)
    return content


def ensure_history_content_safe(content: str) -> str:
    """Reject multimodal payloads and base64 data before persistence."""
    if not isinstance(content, str):
        raise MediaValidationError("Chat history accepts text only; use an image placeholder")
    if DATA_IMAGE_PATTERN.search(content):
        raise MediaValidationError("Base64 image data must never be persisted in chat history")
    return content


def estimate_message_tokens(messages: list[dict], model: str = "") -> int:
    """Count text with a model-aware tokenizer and images with a bounded fixed cost."""
    text_tokens = 0
    image_count = 0
    for message in messages:
        content = message.get("content", "")
        if isinstance(content, str):
            if DATA_IMAGE_PATTERN.search(content):
                raise MediaValidationError("A base64 image was stringified into message text")
            text_tokens += count_text_tokens(content, model)
            continue
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                text = str(part.get("text", ""))
                if DATA_IMAGE_PATTERN.search(text):
                    raise MediaValidationError("A base64 image was placed in a text part")
                text_tokens += count_text_tokens(text, model)
            elif part.get("type") in {"image_url", "image"}:
                image_count += 1
    return text_tokens + image_count * IMAGE_TOKEN_ESTIMATE
