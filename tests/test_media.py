import asyncio
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from PIL import Image

from catgirl.media import (
    IMAGE_TOKEN_ESTIMATE,
    MAX_IMAGE_DIMENSION,
    MAX_IMAGES_PER_MESSAGE,
    MediaValidationError,
    build_history_content,
    build_multimodal_user_content,
    ensure_history_content_safe,
    estimate_message_tokens,
    normalize_image_bytes,
    parse_cq_message,
    sniff_image_mime,
)
from catgirl.media_runtime import MediaReceiver


def image_bytes(format_name: str, size: tuple[int, int] = (32, 32)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, (20, 120, 220)).save(output, format=format_name)
    return output.getvalue()


def test_cq_image_and_mface_urls_are_extracted_before_html_unescape() -> None:
    raw = (
        "看图[CQ:image,file=x,url=https://img.example/a.png]"
        "[CQ:mface,summary=&#91;动画&#93;,url=https://img.example/b.gif?x=1&amp;y=2]"
    )
    text, urls = parse_cq_message(raw)

    assert text == "看图"
    assert urls == ["https://img.example/a.png", "https://img.example/b.gif?x=1&y=2"]


def test_real_bytes_override_declared_mime_and_gif_becomes_static_png() -> None:
    png = image_bytes("PNG")
    assert sniff_image_mime(png, "image/jpeg") == "image/png"

    gif = normalize_image_bytes(image_bytes("GIF"), "application/octet-stream")
    assert gif.mime_type == "image/png"
    assert gif.transformed is True


def test_small_file_with_extreme_dimension_is_resized_before_model_request() -> None:
    normalized = normalize_image_bytes(image_bytes("PNG", (5000, 20)), "image/png")

    assert normalized.transformed is True
    assert max(normalized.width, normalized.height) <= MAX_IMAGE_DIMENSION


def test_request_image_is_structured_but_history_only_gets_placeholders() -> None:
    image = normalize_image_bytes(image_bytes("JPEG"), "image/jpeg")
    request_content = build_multimodal_user_content("这是什么？", [image])
    history_content = build_history_content("这是什么？", ["received/one.jpg"])

    assert isinstance(request_content, list)
    assert request_content[0]["type"] == "image_url"
    assert request_content[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert history_content == "[图片1: one.jpg]\n这是什么？"
    assert "base64" not in history_content


def test_base64_can_never_be_persisted_or_counted_as_text() -> None:
    huge_base64 = "A" * 400_000
    data_uri = f"data:image/jpeg;base64,{huge_base64}"

    with pytest.raises(MediaValidationError, match="never be persisted"):
        ensure_history_content_safe(data_uri)
    with pytest.raises(MediaValidationError, match="stringified"):
        estimate_message_tokens([{"role": "user", "content": data_uri}])

    structured = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": data_uri}}]}]
    assert estimate_message_tokens(structured) == IMAGE_TOKEN_ESTIMATE

    text_part = [{"role": "user", "content": [{"type": "text", "text": data_uri}]}]
    with pytest.raises(MediaValidationError, match="text part"):
        estimate_message_tokens(text_part)


def test_images_per_message_are_bounded() -> None:
    image = normalize_image_bytes(image_bytes("JPEG"), "image/jpeg")

    with pytest.raises(MediaValidationError, match="at most"):
        build_multimodal_user_content("太多图片", [image] * (MAX_IMAGES_PER_MESSAGE + 1))


def test_media_receiver_downloads_normalizes_and_persists_only_file_bytes(tmp_path: Path) -> None:
    source = image_bytes("PNG", (48, 32))

    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://qq-image.test/one.png"
        return httpx.Response(
            200,
            content=source,
            headers={"Content-Type": "image/png", "Content-Length": str(len(source))},
        )

    receiver = MediaReceiver(tmp_path, transport=httpx.MockTransport(handler))
    received = asyncio.run(receiver.download_images(["https://qq-image.test/one.png"]))[0]
    saved = tmp_path / received.ref
    assert saved.is_file()
    assert saved.read_bytes() == received.normalized.data
    assert received.ref.startswith("media/received/")
    assert "base64" not in received.ref


def test_media_receiver_rejects_declared_oversized_download(tmp_path: Path) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"small",
            headers={"Content-Type": "image/png", "Content-Length": str(20 * 1024 * 1024)},
        )

    receiver = MediaReceiver(tmp_path, transport=httpx.MockTransport(handler))
    with pytest.raises(MediaValidationError, match="大小上限"):
        asyncio.run(receiver.download_images(["https://qq-image.test/huge.png"]))
