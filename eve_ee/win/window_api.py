from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

import numpy as np

try:
    import ctypes
    from ctypes import wintypes

    _HAS_CTYPES = True
except Exception:  # pragma: no cover
    ctypes = None  # type: ignore
    wintypes = None  # type: ignore
    _HAS_CTYPES = False


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str


RectLTRB = Tuple[int, int, int, int]


def _require_windows() -> None:
    if os.name != "nt" or not _HAS_CTYPES:
        raise RuntimeError("该功能仅支持 Windows（需要 ctypes）")


def list_top_level_windows(*, include_minimized: bool = True) -> List[WindowInfo]:
    """枚举可见的顶层窗口（用于让用户选择目标窗口）。"""
    _require_windows()

    user32 = ctypes.WinDLL("user32", use_last_error=True)

    EnumWindows = user32.EnumWindows
    EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
    EnumWindows.restype = wintypes.BOOL

    IsWindowVisible = user32.IsWindowVisible
    IsWindowVisible.argtypes = [wintypes.HWND]
    IsWindowVisible.restype = wintypes.BOOL

    IsIconic = user32.IsIconic
    IsIconic.argtypes = [wintypes.HWND]
    IsIconic.restype = wintypes.BOOL

    GetWindowTextLengthW = user32.GetWindowTextLengthW
    GetWindowTextLengthW.argtypes = [wintypes.HWND]
    GetWindowTextLengthW.restype = ctypes.c_int

    GetWindowTextW = user32.GetWindowTextW
    GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    GetWindowTextW.restype = ctypes.c_int

    GetWindowThreadProcessId = user32.GetWindowThreadProcessId
    GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    GetWindowThreadProcessId.restype = wintypes.DWORD

    cur_pid = os.getpid()
    out: List[WindowInfo] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _enum_proc(hwnd: wintypes.HWND, lparam: wintypes.LPARAM) -> wintypes.BOOL:  # noqa: ARG001
        try:
            if not IsWindowVisible(hwnd):
                return True

            if not include_minimized and bool(IsIconic(hwnd)):
                return True

            # 过滤掉本进程窗口，避免用户误选本工具
            pid = wintypes.DWORD(0)
            GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if int(pid.value) == int(cur_pid):
                return True

            length = int(GetWindowTextLengthW(hwnd))
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            GetWindowTextW(hwnd, buf, length + 1)
            title = str(buf.value).strip()
            if not title:
                return True

            out.append(WindowInfo(hwnd=int(hwnd), title=title))
            return True
        except Exception:
            # 不让单个窗口异常影响枚举
            return True

    EnumWindows(_enum_proc, 0)
    out.sort(key=lambda w: w.title.lower())
    return out


def is_window(hwnd: int) -> bool:
    _require_windows()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    IsWindow = user32.IsWindow
    IsWindow.argtypes = [wintypes.HWND]
    IsWindow.restype = wintypes.BOOL
    return bool(IsWindow(wintypes.HWND(int(hwnd))))


def get_window_title(hwnd: int) -> str:
    _require_windows()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    GetWindowTextLengthW = user32.GetWindowTextLengthW
    GetWindowTextLengthW.argtypes = [wintypes.HWND]
    GetWindowTextLengthW.restype = ctypes.c_int

    GetWindowTextW = user32.GetWindowTextW
    GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    GetWindowTextW.restype = ctypes.c_int

    length = int(GetWindowTextLengthW(wintypes.HWND(int(hwnd))))
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    GetWindowTextW(wintypes.HWND(int(hwnd)), buf, length + 1)
    return str(buf.value).strip()


def get_dpi_for_window(hwnd: int) -> int:
    """返回窗口 DPI；失败时返回 96。"""
    _require_windows()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    try:
        GetDpiForWindow = user32.GetDpiForWindow
    except AttributeError:
        return 96
    GetDpiForWindow.argtypes = [wintypes.HWND]
    GetDpiForWindow.restype = wintypes.UINT
    try:
        dpi = int(GetDpiForWindow(wintypes.HWND(int(hwnd))))
        return dpi if dpi > 0 else 96
    except Exception:
        return 96


def get_window_rect_ltrb(hwnd: int) -> RectLTRB:
    """获取窗口在屏幕坐标下的矩形（Left, Top, Right, Bottom）。"""
    _require_windows()
    user32 = ctypes.WinDLL("user32", use_last_error=True)

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    GetWindowRect = user32.GetWindowRect
    GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    GetWindowRect.restype = wintypes.BOOL

    rect = RECT()
    ok = bool(GetWindowRect(wintypes.HWND(int(hwnd)), ctypes.byref(rect)))
    if not ok:
        raise RuntimeError("GetWindowRect 失败")
    return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))


def activate_window(hwnd: int) -> None:
    """尽力将窗口激活/置前（用于便于用户框选区域）。"""
    _require_windows()
    user32 = ctypes.WinDLL("user32", use_last_error=True)

    IsIconic = user32.IsIconic
    IsIconic.argtypes = [wintypes.HWND]
    IsIconic.restype = wintypes.BOOL

    ShowWindow = user32.ShowWindow
    ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    ShowWindow.restype = wintypes.BOOL

    SetForegroundWindow = user32.SetForegroundWindow
    SetForegroundWindow.argtypes = [wintypes.HWND]
    SetForegroundWindow.restype = wintypes.BOOL

    BringWindowToTop = user32.BringWindowToTop
    BringWindowToTop.argtypes = [wintypes.HWND]
    BringWindowToTop.restype = wintypes.BOOL

    _hwnd = wintypes.HWND(int(hwnd))
    try:
        if bool(IsIconic(_hwnd)):
            # SW_RESTORE = 9
            ShowWindow(_hwnd, 9)
        BringWindowToTop(_hwnd)
        SetForegroundWindow(_hwnd)
    except Exception:
        # 置前有系统限制，失败不致命
        return


def capture_window_rgb(hwnd: int) -> np.ndarray:
    """抓取指定窗口的图像（即使窗口被遮挡也尽量可用），返回 RGB ndarray(H, W, 3)。"""
    _require_windows()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

    # 依赖窗口矩形作为输出大小（全窗口：含边框/标题栏）
    left, top, right, bottom = get_window_rect_ltrb(hwnd)
    width = int(right - left)
    height = int(bottom - top)
    if width <= 1 or height <= 1:
        raise RuntimeError("窗口尺寸异常，无法抓取")

    # Win32 常量
    BI_RGB = 0
    DIB_RGB_COLORS = 0
    PW_CLIENTONLY = 0x00000001
    PW_RENDERFULLCONTENT = 0x00000002

    GetWindowDC = user32.GetWindowDC
    GetWindowDC.argtypes = [wintypes.HWND]
    GetWindowDC.restype = wintypes.HDC

    ReleaseDC = user32.ReleaseDC
    ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    ReleaseDC.restype = ctypes.c_int

    PrintWindow = user32.PrintWindow
    PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    PrintWindow.restype = wintypes.BOOL

    CreateCompatibleDC = gdi32.CreateCompatibleDC
    CreateCompatibleDC.argtypes = [wintypes.HDC]
    CreateCompatibleDC.restype = wintypes.HDC

    CreateCompatibleBitmap = gdi32.CreateCompatibleBitmap
    CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    CreateCompatibleBitmap.restype = wintypes.HBITMAP

    SelectObject = gdi32.SelectObject
    SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    SelectObject.restype = wintypes.HGDIOBJ

    DeleteObject = gdi32.DeleteObject
    DeleteObject.argtypes = [wintypes.HGDIOBJ]
    DeleteObject.restype = wintypes.BOOL

    DeleteDC = gdi32.DeleteDC
    DeleteDC.argtypes = [wintypes.HDC]
    DeleteDC.restype = wintypes.BOOL

    GetDIBits = gdi32.GetDIBits
    GetDIBits.argtypes = [
        wintypes.HDC,
        wintypes.HBITMAP,
        wintypes.UINT,
        wintypes.UINT,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.UINT,
    ]
    GetDIBits.restype = ctypes.c_int

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

    _hwnd = wintypes.HWND(int(hwnd))
    hdc_window = GetWindowDC(_hwnd)
    if not hdc_window:
        raise RuntimeError("GetWindowDC 失败")
    hdc_mem = CreateCompatibleDC(hdc_window)
    if not hdc_mem:
        ReleaseDC(_hwnd, hdc_window)
        raise RuntimeError("CreateCompatibleDC 失败")

    hbm = CreateCompatibleBitmap(hdc_window, width, height)
    if not hbm:
        DeleteDC(hdc_mem)
        ReleaseDC(_hwnd, hdc_window)
        raise RuntimeError("CreateCompatibleBitmap 失败")

    old = SelectObject(hdc_mem, hbm)
    try:
        ok = bool(PrintWindow(_hwnd, hdc_mem, PW_RENDERFULLCONTENT))
        if not ok:
            # 回退：更保守的 flags
            ok = bool(PrintWindow(_hwnd, hdc_mem, 0))
        if not ok:
            # 再回退：仅客户端（部分窗口只在该模式下有效）
            ok = bool(PrintWindow(_hwnd, hdc_mem, PW_CLIENTONLY))
        if not ok:
            raise RuntimeError("PrintWindow 失败（窗口可能被最小化/使用特殊渲染）")

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = width
        # 负数表示 top-down，避免图像上下颠倒
        bmi.bmiHeader.biHeight = -height
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB

        buf_size = width * height * 4
        buffer = ctypes.create_string_buffer(buf_size)
        scanlines = GetDIBits(
            hdc_mem,
            hbm,
            0,
            height,
            buffer,
            ctypes.byref(bmi),
            DIB_RGB_COLORS,
        )
        if scanlines != height:
            raise RuntimeError("GetDIBits 失败")

        img = np.frombuffer(buffer, dtype=np.uint8).reshape((height, width, 4))  # BGRA
        rgb = img[..., :3][:, :, ::-1].copy()  # -> RGB
        # 部分窗口（尤其是硬件加速/游戏/Overlay）会“抓取成功但全黑”
        # 这种情况下继续 OCR 只会让用户误以为监控失效，因此直接报错提示。
        if rgb.size > 0 and int(rgb.max()) == 0:
            raise RuntimeError("窗口抓取结果全黑：该窗口可能不支持后台抓取（PrintWindow）。")
        return rgb
    finally:
        try:
            SelectObject(hdc_mem, old)
        except Exception:
            pass
        try:
            DeleteObject(hbm)
        except Exception:
            pass
        try:
            DeleteDC(hdc_mem)
        except Exception:
            pass
        try:
            ReleaseDC(_hwnd, hdc_window)
        except Exception:
            pass


def get_window_rect_dips(hwnd: int) -> RectLTRB:
    """获取窗口矩形并转换为 DIP(96dpi) 坐标，用于与 Qt 的屏幕坐标对齐。"""
    left, top, right, bottom = get_window_rect_ltrb(hwnd)
    dpi = get_dpi_for_window(hwnd)
    scale = float(dpi) / 96.0 if dpi > 0 else 1.0
    if scale <= 0:
        scale = 1.0
    return (
        int(round(left / scale)),
        int(round(top / scale)),
        int(round(right / scale)),
        int(round(bottom / scale)),
    )

