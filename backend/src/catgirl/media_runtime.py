from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from .media import (
    MAX_IMAGES_PER_MESSAGE,
    MAX_SOURCE_BYTES,
    MediaValidationError,
    NormalizedImage,
    normalize_image_bytes,
)


@dataclass(frozen=True)
class ReceivedImage:
    ref: str
    name: str
    normalized: NormalizedImage


class MediaReceiver:
    def __init__(
        self,
        data_dir: Path,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.data_dir = data_dir.resolve()
        self.received_dir = self.data_dir / "media" / "received"
        self.timeout = httpx.Timeout(timeout_seconds, connect=min(10.0, timeout_seconds))
        self.transport = transport

    async def download_images(self, urls: list[str]) -> list[ReceivedImage]:
        if len(urls) > MAX_IMAGES_PER_MESSAGE:
            raise MediaValidationError(f"一条消息最多接收 {MAX_IMAGES_PER_MESSAGE} 张图片")
        results = []
        async with httpx.AsyncClient(
            timeout=self.timeout,
            transport=self.transport,
            follow_redirects=True,
        ) as client:
            for url in urls:
                results.append(await self._download_one(client, url))
        return results

    async def _download_one(self, client: httpx.AsyncClient, url: str) -> ReceivedImage:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            raise MediaValidationError("QQ 图片 URL 无效")
        try:
            async with client.stream("GET", url, headers={"Accept": "image/*"}) as response:
                response.raise_for_status()
                declared_length = response.headers.get("content-length")
                if declared_length and int(declared_length) > MAX_SOURCE_BYTES:
                    raise MediaValidationError("QQ 图片超过源文件大小上限")
                buffer = bytearray()
                async for chunk in response.aiter_bytes():
                    buffer.extend(chunk)
                    if len(buffer) > MAX_SOURCE_BYTES:
                        raise MediaValidationError("QQ 图片超过源文件大小上限")
                declared_mime = response.headers.get("content-type", "").split(";", 1)[0].strip()
        except MediaValidationError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise MediaValidationError(f"QQ 图片下载失败：{type(exc).__name__}") from exc

        normalized = await asyncio.to_thread(
            normalize_image_bytes,
            bytes(buffer),
            declared_mime or None,
        )
        extension = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/gif": ".gif",
            "image/webp": ".webp",
        }[normalized.mime_type]
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        directory = self.received_dir / day
        directory.mkdir(parents=True, exist_ok=True)
        name = f"{uuid4().hex}{extension}"
        path = (directory / name).resolve()
        if self.data_dir not in path.parents:
            raise MediaValidationError("QQ 图片保存路径越界")
        path.write_bytes(normalized.data)
        return ReceivedImage(
            ref=path.relative_to(self.data_dir).as_posix(),
            name=name,
            normalized=normalized,
        )
