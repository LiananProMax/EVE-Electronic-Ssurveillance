"""Windows 窗口 API 封装模块。

提供以下核心功能：
    - 枚举顶层窗口
    - 获取窗口信息（句柄、标题、矩形）
    - 激活/置前窗口
    - 后台窗口截图（即使被遮挡）

技术说明:
    - 使用 ctypes 直接调用 Windows API，无需额外依赖
    - 支持高 DPI 环境（Per-Monitor DPI Aware）
    - 使用 DWM API 获取精确窗口边界（排除阴影）
    - 使用 PrintWindow 实现后台截图

平台要求:
    - Windows 10/11（Windows 7/8 可能部分功能不可用）
    - 需要 ctypes 支持

坐标系说明:
    - 物理坐标: Windows API 返回的实际像素坐标
    - 逻辑坐标 (DIP): 基于 96 DPI 的设备无关像素
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

import numpy as np

# ctypes 导入（仅 Windows 可用）
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
    """窗口信息数据类。

    Attributes:
        hwnd: 窗口句柄（HWND），Windows 中窗口的唯一标识符。
        title: 窗口标题文本。
    """
    hwnd: int
    title: str


# 类型别名：矩形坐标 (Left, Top, Right, Bottom)
RectLTRB = Tuple[int, int, int, int]


def _require_windows() -> None:
    """检查是否在 Windows 环境下运行。

    Raises:
        RuntimeError: 如果不是 Windows 或 ctypes 不可用。
    """
    if os.name != "nt" or not _HAS_CTYPES:
        raise RuntimeError("该功能仅支持 Windows（需要 ctypes）")


def list_top_level_windows(*, include_minimized: bool = True) -> List[WindowInfo]:
    """枚举所有可见的顶层窗口。

    用于让用户从窗口列表中选择目标窗口。
    自动过滤：不可见窗口、无标题窗口、本进程窗口。

    Args:
        include_minimized: 是否包含最小化的窗口。
                          True 表示包含，False 表示排除。

    Returns:
        WindowInfo 列表，按标题字母顺序排序。

    Raises:
        RuntimeError: 如果不是 Windows 平台。

    Note:
        - 过滤本进程窗口是为了避免用户误选择监控工具本身
        - 结果按标题排序，方便用户查找
    """
    _require_windows()

    user32 = ctypes.WinDLL("user32", use_last_error=True)

    # 定义 API 函数签名
    EnumWindows = user32.EnumWindows
    EnumWindows.argtypes = [
        ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM),
        wintypes.LPARAM
    ]
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
        """EnumWindows 回调函数：过滤并收集符合条件的窗口。"""
        try:
            # 过滤不可见窗口
            if not IsWindowVisible(hwnd):
                return True

            # 根据参数过滤最小化窗口
            if not include_minimized and bool(IsIconic(hwnd)):
                return True

            # 过滤本进程窗口，避免用户误选监控工具本身
            pid = wintypes.DWORD(0)
            GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if int(pid.value) == cur_pid:
                return True

            # 获取窗口标题
            length = int(GetWindowTextLengthW(hwnd))
            if length <= 0:
                return True  # 无标题窗口跳过

            buf = ctypes.create_unicode_buffer(length + 1)
            GetWindowTextW(hwnd, buf, length + 1)
            title = str(buf.value).strip()

            if not title:
                return True  # 空标题跳过

            out.append(WindowInfo(hwnd=int(hwnd), title=title))
            return True
        except Exception:
            # 单个窗口异常不影响整体枚举
            return True

    EnumWindows(_enum_proc, 0)
    # 按标题排序，方便用户查找
    out.sort(key=lambda w: w.title.lower())
    return out


def is_window(hwnd: int) -> bool:
    """检查窗口句柄是否有效。

    Args:
        hwnd: 窗口句柄。

    Returns:
        True 如果窗口存在且有效，False 否则。

    Note:
        窗口可能在检查后立即被关闭，此函数仅提供即时状态。
    """
    _require_windows()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    IsWindow = user32.IsWindow
    IsWindow.argtypes = [wintypes.HWND]
    IsWindow.restype = wintypes.BOOL
    return bool(IsWindow(wintypes.HWND(int(hwnd))))


def get_window_title(hwnd: int) -> str:
    """获取窗口标题文本。

    Args:
        hwnd: 窗口句柄。

    Returns:
        窗口标题字符串，如果获取失败或无标题则返回空字符串。
    """
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
    """获取窗口的 DPI 值。

    使用 GetDpiForWindow API（Windows 10 1607+）。

    Args:
        hwnd: 窗口句柄。

    Returns:
        窗口的 DPI 值（如 96, 120, 144, 192 等）。
        如果 API 不可用或调用失败，返回默认值 96。

    Note:
        96 DPI = 100% 缩放
        120 DPI = 125% 缩放
        144 DPI = 150% 缩放
        192 DPI = 200% 缩放
    """
    _require_windows()
    user32 = ctypes.WinDLL("user32", use_last_error=True)

    try:
        GetDpiForWindow = user32.GetDpiForWindow
    except AttributeError:
        # Windows 10 1607 之前的版本没有此 API
        return 96

    GetDpiForWindow.argtypes = [wintypes.HWND]
    GetDpiForWindow.restype = wintypes.UINT

    try:
        dpi = int(GetDpiForWindow(wintypes.HWND(int(hwnd))))
        return dpi if dpi > 0 else 96
    except Exception:
        return 96


def get_window_rect_ltrb(hwnd: int, *, exclude_shadow: bool = True) -> tuple[RectLTRB, bool]:
    """获取窗口在屏幕坐标下的矩形。

    坐标格式为 (Left, Top, Right, Bottom)，均为物理像素。

    Args:
        hwnd: 窗口句柄。
        exclude_shadow: 是否排除窗口阴影。
            - True (默认): 使用 DwmGetWindowAttribute 获取精确边界
            - False: 使用 GetWindowRect，可能包含阴影区域

    Returns:
        ((left, top, right, bottom), used_dwm) 元组:
        - rect: 窗口矩形的物理像素坐标
        - used_dwm: True 表示成功使用了 DWM API（精确边界），False 表示回退到 GetWindowRect

    Raises:
        RuntimeError: 如果两种方法都失败。

    Note:
        Windows 10/11 的窗口有系统级阴影，GetWindowRect 返回的矩形会偏大。
        使用 DwmGetWindowAttribute + DWMWA_EXTENDED_FRAME_BOUNDS 可获取实际可见边界。
    """
    _require_windows()
    user32 = ctypes.WinDLL("user32", use_last_error=True)

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    _hwnd = wintypes.HWND(int(hwnd))
    rect = RECT()

    # 方法一：使用 DWM API 获取不含阴影的精确边界
    if exclude_shadow:
        try:
            dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
            DwmGetWindowAttribute = dwmapi.DwmGetWindowAttribute
            DWMWA_EXTENDED_FRAME_BOUNDS = 9  # 获取扩展框架边界

            hr = DwmGetWindowAttribute(
                _hwnd,
                DWMWA_EXTENDED_FRAME_BOUNDS,
                ctypes.byref(rect),
                ctypes.sizeof(rect)
            )
            if hr == 0:  # S_OK
                return (
                    (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)),
                    True
                )
        except (OSError, AttributeError):
            pass  # DWM 不可用，回退到 GetWindowRect

    # 方法二（回退）：使用 GetWindowRect（可能包含阴影）
    GetWindowRect = user32.GetWindowRect
    GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    GetWindowRect.restype = wintypes.BOOL

    if not bool(GetWindowRect(_hwnd, ctypes.byref(rect))):
        raise RuntimeError(f"GetWindowRect 失败 (hwnd={hwnd})")

    return (
        (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)),
        False
    )


def activate_window(hwnd: int) -> None:
    """尽力将窗口激活并置于最前。

    执行以下操作（按顺序）:
        1. 如果窗口最小化，先恢复
        2. 将窗口置于 Z 顺序顶部
        3. 设置为前台窗口

    Args:
        hwnd: 窗口句柄。

    Note:
        - 此函数不会抛出异常，失败时静默返回
        - Windows 有前台窗口保护机制，不保证一定能成功置前
        - 主要用于方便用户看到目标窗口并进行框选操作
    """
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
        # 如果窗口最小化，先恢复
        if bool(IsIconic(_hwnd)):
            SW_RESTORE = 9
            ShowWindow(_hwnd, SW_RESTORE)

        # 置于 Z 顺序顶部
        BringWindowToTop(_hwnd)

        # 设置为前台窗口
        SetForegroundWindow(_hwnd)
    except Exception:
        # 置前操作有系统限制，失败不致命
        pass


def capture_window_rgb(hwnd: int) -> np.ndarray:
    """抓取指定窗口的图像，即使窗口被遮挡也能正常工作。

    使用 PrintWindow API 实现后台窗口截图，不受窗口遮挡影响。

    Args:
        hwnd: 窗口句柄。

    Returns:
        RGB 图像的 numpy 数组，形状为 (height, width, 3)，dtype=uint8。

    Raises:
        RuntimeError:
            - 窗口尺寸异常（宽或高 <= 1）
            - GDI 资源创建失败
            - PrintWindow 调用失败
            - 抓取结果全黑（通常表示窗口使用硬件加速渲染）

    Note:
        以下类型的窗口可能无法正常抓取:
        - 使用 DirectX/OpenGL/Vulkan 等硬件加速的窗口
        - 游戏/视频播放器等使用 Overlay 的窗口
        - 某些 UWP/WinUI 应用

    技术细节:
        1. 获取窗口 DC
        2. 创建兼容 DC 和位图
        3. 使用 PrintWindow 将窗口内容绘制到位图
        4. 使用 GetDIBits 读取位图像素数据
        5. 转换 BGRA -> RGB
    """
    _require_windows()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

    # 获取窗口尺寸（使用包含边框的完整矩形）
    # 注意：PrintWindow 抓取的是整个窗口，包括边框和标题栏
    (left, top, right, bottom), _ = get_window_rect_ltrb(hwnd, exclude_shadow=False)
    width = int(right - left)
    height = int(bottom - top)

    if width <= 1 or height <= 1:
        raise RuntimeError(f"窗口尺寸异常 ({width}x{height})，无法抓取")

    # ------------------------------------
    # Win32 常量
    # ------------------------------------
    BI_RGB = 0              # 未压缩 RGB
    DIB_RGB_COLORS = 0      # 颜色表包含 RGB 值
    PW_CLIENTONLY = 0x00000001      # 仅抓取客户区
    PW_RENDERFULLCONTENT = 0x00000002  # 包含完整渲染内容（Win 8.1+）

    # ------------------------------------
    # API 函数声明
    # ------------------------------------
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
        wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
        wintypes.LPVOID, wintypes.LPVOID, wintypes.UINT,
    ]
    GetDIBits.restype = ctypes.c_int

    # ------------------------------------
    # 位图信息结构体
    # ------------------------------------
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

    # ------------------------------------
    # 创建 GDI 资源
    # ------------------------------------
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

    old_obj = SelectObject(hdc_mem, hbm)

    try:
        # ------------------------------------
        # 使用 PrintWindow 抓取窗口内容
        # ------------------------------------
        # 尝试多种模式，从最完整到最保守
        capture_ok = bool(PrintWindow(_hwnd, hdc_mem, PW_RENDERFULLCONTENT))

        if not capture_ok:
            # 回退：基础模式
            capture_ok = bool(PrintWindow(_hwnd, hdc_mem, 0))

        if not capture_ok:
            # 再回退：仅客户区（部分窗口只在此模式下有效）
            capture_ok = bool(PrintWindow(_hwnd, hdc_mem, PW_CLIENTONLY))

        if not capture_ok:
            raise RuntimeError(
                "PrintWindow 失败（窗口可能被最小化或使用特殊渲染）"
            )

        # ------------------------------------
        # 读取位图像素数据
        # ------------------------------------
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = width
        bmi.bmiHeader.biHeight = -height  # 负数 = top-down，避免图像上下颠倒
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32  # BGRA
        bmi.bmiHeader.biCompression = BI_RGB

        buf_size = width * height * 4
        buffer = ctypes.create_string_buffer(buf_size)

        scanlines = GetDIBits(
            hdc_mem, hbm, 0, height,
            buffer, ctypes.byref(bmi), DIB_RGB_COLORS
        )

        if scanlines != height:
            raise RuntimeError(f"GetDIBits 失败（期望 {height} 行，实际 {scanlines} 行）")

        # ------------------------------------
        # 转换为 RGB numpy 数组
        # ------------------------------------
        # 原始数据为 BGRA，需要转换为 RGB
        img_bgra = np.frombuffer(buffer, dtype=np.uint8).reshape((height, width, 4))
        rgb = img_bgra[..., :3][:, :, ::-1].copy()  # BGRA -> RGB

        # 检测全黑结果（硬件加速窗口的典型症状）
        if rgb.size > 0 and int(rgb.max()) == 0:
            raise RuntimeError(
                "窗口抓取结果全黑：该窗口可能使用硬件加速渲染，不支持 PrintWindow 后台抓取。"
            )

        return rgb

    finally:
        # ------------------------------------
        # 清理 GDI 资源（确保不泄漏）
        # ------------------------------------
        try:
            SelectObject(hdc_mem, old_obj)
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


def _get_dpi_for_point(x: int, y: int) -> int:
    """获取指定屏幕坐标点所在显示器的 DPI。

    Args:
        x: 屏幕 X 坐标（物理像素）。
        y: 屏幕 Y 坐标（物理像素）。

    Returns:
        该点所在显示器的有效 DPI 值。
        如果获取失败，返回默认值 96。

    Note:
        使用 MonitorFromPoint + GetDpiForMonitor API (Windows 8.1+)。
    """
    _require_windows()
    user32 = ctypes.WinDLL("user32", use_last_error=True)

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    MONITOR_DEFAULTTONEAREST = 2  # 如果点不在任何显示器上，返回最近的

    try:
        # 获取点所在的显示器句柄
        MonitorFromPoint = user32.MonitorFromPoint
        MonitorFromPoint.argtypes = [POINT, wintypes.DWORD]
        MonitorFromPoint.restype = wintypes.HANDLE

        pt = POINT(int(x), int(y))
        hMonitor = MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)

        if hMonitor:
            # 使用 GetDpiForMonitor 获取显示器 DPI (Windows 8.1+)
            try:
                shcore = ctypes.WinDLL("shcore", use_last_error=True)
                GetDpiForMonitor = shcore.GetDpiForMonitor
                MDT_EFFECTIVE_DPI = 0
                dpiX = wintypes.UINT()
                dpiY = wintypes.UINT()

                hr = GetDpiForMonitor(hMonitor, MDT_EFFECTIVE_DPI, ctypes.byref(dpiX), ctypes.byref(dpiY))
                if hr == 0:  # S_OK
                    return int(dpiX.value) if dpiX.value > 0 else 96
            except (AttributeError, OSError):
                pass  # API 不可用

    except (AttributeError, OSError):
        pass  # MonitorFromPoint 不可用

    return 96  # 默认 DPI


def _physical_to_logical_point(hwnd: int, x: int, y: int) -> Tuple[int, int]:
    """将物理像素坐标转换为逻辑坐标（DIP）。

    在多显示器不同 DPI 环境下，正确处理每个点所在显示器的 DPI 差异。

    Args:
        hwnd: 窗口句柄（用于 PhysicalToLogicalPointForPerMonitorDPI）。
        x: 物理像素 X 坐标。
        y: 物理像素 Y 坐标。

    Returns:
        (逻辑X, 逻辑Y) 元组，基于 96 DPI。

    转换策略:
        1. 优先使用 PhysicalToLogicalPointForPerMonitorDPI (Windows 8.1+)
           该 API 能正确处理每个点所在显示器的 DPI
        2. 回退：使用该点所在显示器的 DPI 手动缩放
    """
    _require_windows()
    user32 = ctypes.WinDLL("user32", use_last_error=True)

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    # 方法一：使用 Windows API 直接转换
    try:
        PhysicalToLogicalPointForPerMonitorDPI = user32.PhysicalToLogicalPointForPerMonitorDPI
        PhysicalToLogicalPointForPerMonitorDPI.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
        PhysicalToLogicalPointForPerMonitorDPI.restype = wintypes.BOOL

        pt = POINT(int(x), int(y))
        if PhysicalToLogicalPointForPerMonitorDPI(wintypes.HWND(int(hwnd)), ctypes.byref(pt)):
            return (int(pt.x), int(pt.y))
    except (AttributeError, OSError):
        pass  # API 不可用（Windows 8 或更早）

    # 方法二（回退）：根据该点所在显示器的 DPI 手动缩放
    dpi = _get_dpi_for_point(x, y)
    scale = float(dpi) / 96.0 if dpi > 0 else 1.0

    # 防止无效的缩放因子
    if scale <= 0:
        scale = 1.0

    return (int(round(x / scale)), int(round(y / scale)))


def get_window_rect_dips(hwnd: int) -> RectLTRB:
    """获取窗口矩形并转换为 DIP (96 DPI) 坐标。

    用于与 Qt 的逻辑坐标系对齐。

    Args:
        hwnd: 窗口句柄。

    Returns:
        (left, top, right, bottom) 元组，DIP 坐标。

    Note:
        在多显示器不同 DPI 环境下，左上角和右下角可能在不同 DPI 的显示器上，
        因此需要分别转换每个角点，而不是简单地对整个矩形应用单一缩放。
    """
    (left, top, right, bottom), _ = get_window_rect_ltrb(hwnd)

    # 分别转换左上角和右下角，处理跨显示器的情况
    l_dip, t_dip = _physical_to_logical_point(hwnd, left, top)
    r_dip, b_dip = _physical_to_logical_point(hwnd, right, bottom)

    return (l_dip, t_dip, r_dip, b_dip)

