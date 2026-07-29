from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import threading
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError

from catgirl.plugins import PluginAction, PluginEvent, PluginResult


SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
NON_TRIGGERABLE_CATEGORIES = {"other"}
DEFAULT_ASSET_LIMIT_MB = 16.0
MAX_ASSET_LIMIT_MB = 32.0


def _split(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,，\n]", value) if item.strip()]


class StickerReplyPlugin:
    def __init__(self) -> None:
        self._compression_lock = threading.Lock()

    def admin_action(self, context, action: str, payload: dict) -> dict:
        if action != "open-assets-folder":
            raise ValueError("不支持的管理动作")
        assets = (context.plugin_path / "assets").resolve()
        if context.plugin_path not in assets.parents or not assets.is_dir():
            raise FileNotFoundError("表情文件夹不存在")
        if os.name == "nt":
            os.startfile(str(assets))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(assets)])
        else:
            subprocess.Popen(["xdg-open", str(assets)])
        return {"opened": True}

    def _metadata(self, context) -> dict[str, str]:
        path = context.resolve_asset("memes_data.json")
        data = json.loads(path.read_text("utf-8"))
        return {str(key): str(value) for key, value in data.items()}

    def _assets(self, context, category: str) -> list[Path]:
        try:
            directory = context.resolve_asset(f"assets/{category}/.asset-index")
            directory = directory.parent
        except FileNotFoundError:
            directory = context.plugin_path / "assets" / category
            if not directory.is_dir() or context.plugin_path not in directory.resolve().parents:
                return []
        return [
            item for item in directory.iterdir()
            if item.is_file() and item.suffix.lower() in SUPPORTED_EXTENSIONS
        ]

    def _asset_limit_bytes(self, context) -> int:
        try:
            limit_mb = float(context.settings.get("max_asset_mb", DEFAULT_ASSET_LIMIT_MB))
        except (TypeError, ValueError):
            limit_mb = DEFAULT_ASSET_LIMIT_MB
        limit_mb = max(0.1, min(limit_mb, MAX_ASSET_LIMIT_MB))
        return max(1, round(limit_mb * 1024 * 1024))

    @staticmethod
    def _save_static_candidate(image: Image.Image, path: Path, suffix: str, quality: int) -> None:
        if suffix in {".jpg", ".jpeg"}:
            if image.mode != "RGB":
                image = image.convert("RGB")
            image.save(path, format="JPEG", quality=quality, optimize=True)
            return
        if suffix == ".webp":
            image.save(path, format="WEBP", quality=quality, method=6)
            return
        if suffix == ".gif":
            image.convert("P", palette=Image.Palette.ADAPTIVE, colors=max(16, quality * 3)).save(
                path,
                format="GIF",
                optimize=True,
            )
            return
        if quality >= 80:
            image.save(path, format="PNG", optimize=True, compress_level=9)
        else:
            colors = max(16, min(256, quality * 3))
            image.convert("RGBA").quantize(colors=colors).save(
                path,
                format="PNG",
                optimize=True,
                compress_level=9,
            )

    def _compress_static(self, opened: Image.Image, target: Path, limit: int) -> bool:
        image = ImageOps.exif_transpose(opened).copy()
        width, height = image.size
        for scale in (1.0, 0.85, 0.7, 0.55, 0.4, 0.3, 0.2, 0.1):
            size = (max(1, round(width * scale)), max(1, round(height * scale)))
            working = image if size == image.size else image.resize(size, Image.Resampling.LANCZOS)
            for quality in (85, 70, 55, 40, 25):
                self._save_static_candidate(working, target, target.suffix.lower(), quality)
                if target.stat().st_size <= limit:
                    return True
        return False

    @staticmethod
    def _gif_frames(opened: Image.Image, step: int, scale: float, colors: int) -> tuple[list[Image.Image], list[int]]:
        frames: list[Image.Image] = []
        durations: list[int] = []
        pending_duration = 0
        for index, frame in enumerate(ImageSequence.Iterator(opened)):
            pending_duration += max(1, int(frame.info.get("duration", opened.info.get("duration", 100))))
            if index % step != 0:
                continue
            rgba = frame.convert("RGBA")
            if scale < 1:
                rgba = rgba.resize(
                    (max(1, round(rgba.width * scale)), max(1, round(rgba.height * scale))),
                    Image.Resampling.LANCZOS,
                )
            frames.append(rgba.convert("P", palette=Image.Palette.ADAPTIVE, colors=colors))
            durations.append(pending_duration)
            pending_duration = 0
        if durations and pending_duration:
            durations[-1] += pending_duration
        return frames, durations

    def _compress_animated_gif(self, opened: Image.Image, target: Path, limit: int) -> bool:
        frame_count = max(1, int(getattr(opened, "n_frames", 1)))
        loop = int(opened.info.get("loop", 0))
        for step, scale, colors in (
            (1, 1.0, 256), (1, 0.75, 128), (1, 0.55, 96),
            (2, 0.45, 64), (3, 0.3, 48), (4, 0.2, 32),
            (max(1, frame_count), 0.15, 32),
        ):
            opened.seek(0)
            frames, durations = self._gif_frames(opened, step, scale, colors)
            if not frames:
                continue
            frames[0].save(
                target,
                format="GIF",
                save_all=len(frames) > 1,
                append_images=frames[1:],
                duration=durations,
                loop=loop,
                optimize=True,
                disposal=2,
            )
            if target.stat().st_size <= limit:
                return True
        return False

    def _compress_asset_in_place(self, path: Path, limit: int) -> None:
        temporary = path.with_name(f".{path.stem}.{uuid4().hex}{path.suffix}")
        try:
            with Image.open(path) as opened:
                animated_gif = path.suffix.lower() == ".gif" and bool(
                    getattr(opened, "is_animated", False)
                )
                compressed = (
                    self._compress_animated_gif(opened, temporary, limit)
                    if animated_gif
                    else self._compress_static(opened, temporary, limit)
                )
            if not compressed or not temporary.is_file() or temporary.stat().st_size > limit:
                raise ValueError(f"无法把表情压缩到 {limit / 1024 / 1024:g} MB 以下")
            temporary.replace(path)
        except (OSError, UnidentifiedImageError) as exc:
            raise ValueError(f"表情图片无法压缩：{path.name}") from exc
        finally:
            temporary.unlink(missing_ok=True)

    def _prepare_asset(self, context, path: Path) -> Path:
        limit = self._asset_limit_bytes(context)
        if path.stat().st_size <= limit:
            return path
        with self._compression_lock:
            if path.stat().st_size > limit:
                self._compress_asset_in_place(path, limit)
        return path

    def _choose(self, context, categories: list[str]) -> tuple[str, Path] | None:
        triggerable = set(self._metadata(context)) - NON_TRIGGERABLE_CATEGORIES
        available = [
            (category, assets)
            for category in dict.fromkeys(categories)
            if category in triggerable and (assets := self._assets(context, category))
        ]
        if not available:
            return None
        category, assets = random.choice(available)
        return category, self._prepare_asset(context, random.choice(assets))

    def on_user_message(self, context, event: PluginEvent) -> PluginResult:
        keywords = _split(str(context.settings.get("request_keywords", "")))
        if not any(keyword in event.text for keyword in keywords):
            return PluginResult()
        categories = _split(str(context.settings.get("positive_categories", "")))
        selected = self._choose(context, categories)
        if selected is None:
            return PluginResult(
                actions=[PluginAction(kind="send_text", payload={"conversation_id": event.conversation_id, "text": "当前没有可用的表情资源"})],
                consume=True,
            )
        category, path = selected
        return PluginResult(
            actions=[PluginAction(kind="send_image", payload={"conversation_id": event.conversation_id, "asset_ref": str(path), "category": category})],
            consume=True,
        )

    def before_prompt_compile(self, context, event: PluginEvent) -> PluginResult:
        probability = float(context.settings.get("probability", 0.1))
        preview = event.metadata.get("preview") is True
        if probability <= 0 or (not preview and random.random() > probability):
            return PluginResult()
        metadata = self._metadata(context)
        available = {
            name: description
            for name, description in metadata.items()
            if name not in NON_TRIGGERABLE_CATEGORIES and self._assets(context, name)
        }
        if not available:
            return PluginResult()
        catalog = "\n".join(f"- {name}: {description}" for name, description in available.items())
        content = (
            "你可以在回复中选择至多一个表情。需要使用时输出 <sticker name=\"分类名\"/>；"
            "不需要时不要输出标签。可用分类：\n" + catalog
        )
        payload = {"conversation_id": event.conversation_id, "role": "system", "content": content}
        if preview:
            payload["preview_note"] = f"{probability * 100:g}% 概率"
        return PluginResult(actions=[PluginAction(kind="prompt_addition", payload=payload)])

    def after_model_response(self, context, event: PluginEvent) -> PluginResult:
        category = str(event.metadata.get("sticker_category", "")).strip()
        if not category:
            return PluginResult()
        selected = self._choose(context, [category])
        payload = {"conversation_id": event.conversation_id, "sticker_category": category}
        if selected is not None:
            payload["asset_ref"] = str(selected[1])
        return PluginResult(actions=[PluginAction(kind="replace_response", payload=payload)])


plugin = StickerReplyPlugin()
