"""Windows integration for the local Bilibili reminder.

No browser credentials are read. Session files contain only a DPAPI-protected
cookie dictionary and can be decrypted by the same Windows user on this PC.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import getpass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from typing import Callable


APP_NAME = "定制关注"
RUN_VALUE = "BiliUpReminder"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
SESSION_HEADER = b"BILISESSION1\x00"
_instance_handle = None
_instance_lock = threading.Lock()


def _require_windows() -> None:
    if os.name != "nt":
        raise OSError("此功能仅支持 Windows。")


class SessionError(RuntimeError):
    """Safe error message without credential data."""


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _protect_bytes(payload: bytes, decrypt: bool = False) -> bytes:
    _require_windows()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    source_buffer = ctypes.create_string_buffer(payload)
    source = _DataBlob(len(payload), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = _DataBlob()
    if decrypt:
        method = crypt32.CryptUnprotectData
        method.argtypes = [
            ctypes.POINTER(_DataBlob), ctypes.c_void_p,
            ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(_DataBlob),
        ]
    else:
        method = crypt32.CryptProtectData
        method.argtypes = [
            ctypes.POINTER(_DataBlob), wintypes.LPCWSTR,
            ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(_DataBlob),
        ]
    method.restype = wintypes.BOOL
    description = None if decrypt else "BiliUpReminder local session"
    # CRYPTPROTECT_UI_FORBIDDEN: never open an authentication dialog.
    succeeded = method(ctypes.byref(source), description, None, None, None, 1, ctypes.byref(target))
    try:
        if not succeeded:
            if decrypt:
                raise SessionError("无法解密登录信息，请在当前 Windows 用户下重新扫码登录。")
            raise SessionError("Windows 无法加密登录信息，本次登录未保存。")
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        ctypes.memset(source_buffer, 0, len(payload))
        if target.pbData:
            ctypes.memset(target.pbData, 0, target.cbData)
            kernel32.LocalFree(target.pbData)


def _validate_cookies(cookies: object) -> dict[str, str]:
    if not isinstance(cookies, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in cookies.items()
    ):
        raise SessionError("登录信息格式无效，请重新扫码登录。")
    return cookies


def save_session(path: str | Path, cookies: dict[str, str]) -> None:
    """Atomically save encrypted cookies; never write a plaintext intermediate."""
    cookies = _validate_cookies(cookies)
    encrypted = _protect_bytes(json.dumps(cookies, ensure_ascii=False).encode("utf-8"))
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=".session-", suffix=".tmp", dir=destination.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(SESSION_HEADER + encrypted)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except OSError as exc:
        raise SessionError("无法保存加密登录信息，请检查数据文件夹是否可写。") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_session(path: str | Path) -> dict[str, str]:
    """Missing file means signed out; damaged/inaccessible files are explicit errors."""
    try:
        stored = Path(path).read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise SessionError("无法读取登录信息，请检查数据文件夹权限。") from exc
    if not stored.startswith(SESSION_HEADER):
        raise SessionError("登录信息文件损坏，请重新扫码登录。")
    decrypted = _protect_bytes(stored[len(SESSION_HEADER):], decrypt=True)
    try:
        return _validate_cookies(json.loads(decrypted.decode("utf-8")))
    except (ValueError, UnicodeError) as exc:
        raise SessionError("登录信息文件损坏，请重新扫码登录。") from exc


def startup_command(entry_path: str | Path | None = None) -> str:
    """Quote each argument using Windows CreateProcess command-line rules."""
    if getattr(sys, "frozen", False):
        arguments = [sys.executable, "--background"]
    else:
        entry = Path(entry_path) if entry_path is not None else Path(__file__).with_name("main.py")
        if not entry.is_file():
            raise FileNotFoundError("找不到程序入口，无法设置开机启动。")
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        executable = str(pythonw) if pythonw.is_file() else sys.executable
        arguments = [executable, str(entry.resolve()), "--background"]
    return subprocess.list2cmdline(arguments)


def set_autostart(enabled: bool, entry_path: str | Path | None = None) -> None:
    """Set the current-user Run entry only after an explicit UI choice."""
    _require_windows()
    import winreg
    if enabled:
        command = startup_command(entry_path)
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, command)
    else:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, RUN_VALUE)
        except FileNotFoundError:
            pass


def get_autostart() -> bool:
    _require_windows()
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, kind = winreg.QueryValueEx(key, RUN_VALUE)
            return kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) and bool(value)
    except FileNotFoundError:
        return False


def acquire_instance() -> bool:
    """Keep a user-scoped Windows mutex open for the entire application lifetime."""
    global _instance_handle
    _require_windows()
    with _instance_lock:
        if _instance_handle is not None:
            return True
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        user_key = hashlib.sha256(getpass.getuser().encode("utf-8")).hexdigest()[:16]
        ctypes.set_last_error(0)
        handle = kernel32.CreateMutexW(None, False, "Local\\BiliUpReminder_" + user_key)
        if not handle:
            raise OSError("无法创建程序实例锁。")
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(handle)
            return False
        _instance_handle = handle
        return True


def release_instance() -> None:
    global _instance_handle
    if os.name != "nt":
        return
    with _instance_lock:
        if _instance_handle is not None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.CloseHandle(_instance_handle)
            _instance_handle = None


def make_icon():
    """Return a Pillow image, also reusable for packaging the application icon."""
    from PIL import Image, ImageDraw
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 2, 62, 62), radius=16, fill="#FB7299")
    draw.line((20, 9, 27, 18), fill="white", width=4)
    draw.line((44, 9, 37, 18), fill="white", width=4)
    draw.rounded_rectangle((11, 18, 53, 49), radius=9, fill="white")
    draw.rounded_rectangle((16, 23, 48, 44), radius=5, fill="#74CDF1")
    draw.line((24, 28, 24, 35), fill="white", width=3)
    draw.line((40, 28, 40, 35), fill="white", width=3)
    draw.arc((27, 30, 37, 39), 0, 180, fill="white", width=2)
    draw.ellipse((46, 42, 61, 57), fill="#425EBA", outline="white", width=2)
    return image


class Tray:
    """Callbacks run outside Tk's main thread; callers must enqueue UI work."""

    def __init__(
        self,
        on_show: Callable[[], None],
        on_check: Callable[[], None],
        on_quit: Callable[[], None],
        icon_path: str | Path | None = None,
    ):
        self.on_show = on_show
        self.on_check = on_check
        self.on_quit = on_quit
        self.icon_path = icon_path
        self.image = None
        self.last_error = None
        self._icon = None
        self._thread = None
        self._ready = threading.Event()

    def _callback(self, callback):
        def invoke(icon, item):
            try:
                callback()
            except Exception as exc:
                self.last_error = "托盘操作失败：" + type(exc).__name__
        return invoke

    def start(self) -> bool:
        if self._thread is not None and self._thread.is_alive():
            return self._ready.is_set() and self.last_error is None
        self._ready.clear()
        self.last_error = None
        try:
            import pystray
            from PIL import Image
            if self.icon_path is not None:
                with Image.open(self.icon_path) as source:
                    self.image = source.convert("RGBA")
            else:
                self.image = make_icon()
            self._icon = pystray.Icon(
                "BiliUpReminder", self.image, APP_NAME,
                menu=pystray.Menu(
                    pystray.MenuItem("打开主窗口", self._callback(self.on_show), default=True),
                    pystray.MenuItem("立即检查", self._callback(self.on_check)),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem("完全退出", self._callback(self.on_quit)),
                ),
            )
            def setup(icon):
                icon.visible = True
                self._ready.set()

            def run():
                try:
                    self._icon.run(setup=setup)
                except Exception as exc:
                    self.last_error = "托盘启动失败：" + type(exc).__name__
                    self._ready.set()

            self._thread = threading.Thread(target=run, name="bili-tray", daemon=True)
            self._thread.start()
            ready = self._ready.wait(3)
            if not ready:
                self.last_error = "托盘启动超时，请保留主窗口运行。"
            if self.last_error is not None:
                self.stop()
                return False
            return True
        except Exception as exc:
            self.last_error = "托盘不可用：" + type(exc).__name__
            return False

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception as exc:
                self.last_error = "托盘停止失败：" + type(exc).__name__
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
        self._icon = None

