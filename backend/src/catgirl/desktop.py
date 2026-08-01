from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from ctypes import wintypes
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import uvicorn
import webview

from .config import APPLICATION_ROOT, RESOURCE_ROOT, get_settings
from .main import create_app


LOGGER = logging.getLogger("catgirl.desktop")
WM_SETICON = 0x0080
ICON_SMALL = 0
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
SM_CXSMICON = 49
SM_CYSMICON = 50


def _message_box(message: str, title: str = "一只猫娘") -> None:
    ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)


def _health_ready(url: str) -> bool:
    try:
        with urlopen(f"{url}/health", timeout=0.5) as response:
            return response.status == 200 and b'"service":"catgirl"' in response.read()
    except (OSError, URLError):
        return False


def _set_window_caption_icon(
    window: object,
    icon_path: Path,
    retained_handles: list[int],
) -> None:
    icon_handle = 0
    try:
        if not window.events.shown.wait(10):  # type: ignore[attr-defined]
            raise RuntimeError("WebView 窗口未能及时显示")
        user32 = ctypes.windll.user32
        user32.LoadImageW.argtypes = [
            wintypes.HINSTANCE,
            wintypes.LPCWSTR,
            wintypes.UINT,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.SendMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.SendMessageW.restype = wintypes.LPARAM
        width = max(16, int(user32.GetSystemMetrics(SM_CXSMICON)))
        height = max(16, int(user32.GetSystemMetrics(SM_CYSMICON)))
        loaded = user32.LoadImageW(
            None,
            str(icon_path),
            IMAGE_ICON,
            width,
            height,
            LR_LOADFROMFILE,
        )
        icon_handle = int(ctypes.cast(loaded, ctypes.c_void_p).value or 0)
        if not icon_handle:
            raise ctypes.WinError()
        hwnd = int(window.native.Handle.ToInt64())  # type: ignore[attr-defined]
        user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, icon_handle)
        retained_handles.append(icon_handle)
    except Exception:
        if icon_handle:
            ctypes.windll.user32.DestroyIcon(icon_handle)
        LOGGER.exception("WebView 标题栏小图标设置失败")


def _release_icon_handles(handles: list[int]) -> None:
    destroy_icon = ctypes.windll.user32.DestroyIcon
    destroy_icon.argtypes = [wintypes.HICON]
    for handle in handles:
        destroy_icon(handle)
    handles.clear()


class DesktopServer:
    def __init__(self) -> None:
        settings = get_settings()
        self.url = f"http://127.0.0.1:{settings.port}"
        self.frontend_url = f"{self.url}/?startup={time.time_ns()}"
        self.server = uvicorn.Server(
            uvicorn.Config(
                create_app(),
                host="127.0.0.1",
                port=settings.port,
                log_level=settings.log_level.lower(),
                access_log=False,
            )
        )
        self.thread = threading.Thread(
            target=self.server.run,
            name="catgirl-local-server",
            daemon=True,
        )

    def start(self) -> None:
        if _health_ready(self.url):
            raise RuntimeError("一只猫娘已经在运行，请先关闭已有窗口。")
        self.thread.start()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if _health_ready(self.url):
                return
            if not self.thread.is_alive():
                break
            time.sleep(0.1)
        self.stop()
        raise RuntimeError("本地服务启动失败，请查看 logs/catgirl.log。")

    def stop(self) -> None:
        self.server.should_exit = True
        if self.thread.is_alive():
            self.thread.join(timeout=8)


def main() -> int:
    os.chdir(APPLICATION_ROOT)
    (APPLICATION_ROOT / "data").mkdir(parents=True, exist_ok=True)
    (APPLICATION_ROOT / "logs").mkdir(parents=True, exist_ok=True)
    (APPLICATION_ROOT / "backups").mkdir(parents=True, exist_ok=True)
    storage_path = APPLICATION_ROOT / "data" / "webview"
    storage_path.mkdir(parents=True, exist_ok=True)
    icon_path = RESOURCE_ROOT / "assets" / "catgirl.ico"
    caption_icon_path = RESOURCE_ROOT / "assets" / "catgirl-window-black.ico"
    caption_icon_handles: list[int] = []

    runtime = DesktopServer()
    try:
        runtime.start()
        window = webview.create_window(
            "一只猫娘",
            runtime.frontend_url,
            width=1440,
            height=900,
            min_size=(960, 640),
            maximized=True,
            background_color="#09090d",
            text_select=True,
        )
        webview.start(
            func=_set_window_caption_icon,
            args=(window, caption_icon_path, caption_icon_handles),
            gui="edgechromium",
            private_mode=False,
            storage_path=str(storage_path),
            icon=str(icon_path),
        )
        return 0
    except Exception as exc:
        LOGGER.exception("桌面程序启动失败")
        _message_box(str(exc))
        return 1
    finally:
        _release_icon_handles(caption_icon_handles)
        runtime.stop()
