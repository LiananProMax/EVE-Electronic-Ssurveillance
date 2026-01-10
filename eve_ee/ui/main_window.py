"""主窗口模块。

提供 OCR 监控应用的主界面，包括：
- 窗口/区域选择功能
- 实时预览和监控控制
- 多显示器 DPI 感知的坐标转换

主要类:
    MainWindow: 应用主窗口，集成所有 UI 组件和业务逻辑

坐标系说明:
    - 物理坐标: Windows API 返回的实际像素坐标
    - 逻辑坐标 (DIP): Qt 使用的设备无关像素坐标 (基于 96 DPI)
    - 归一化坐标: 相对于目标窗口的 0~1 范围坐标，用于跨 DPI 稳定定位
"""
from __future__ import annotations

import time
from typing import Any, Optional

from PyQt6 import QtCore, QtGui, QtWidgets

from ..constants import ALARM_AVG_CONF_THRESHOLD
from ..ocr.rapidocr_engine import create_rapidocr_engine
from ..worker import Worker
from .selection_overlay import AreaSelectionOverlay
from .styles import MAIN_STYLESHEET
from .window_picker import pick_window
from ..win.window_api import activate_window, get_window_rect_ltrb, get_window_title, is_window


# ---------------------------------------------------------------------------
# 多显示器坐标映射辅助函数
# ---------------------------------------------------------------------------


def _enumerate_all_monitors() -> list[tuple[tuple[int, int, int, int], int]]:
    """枚举所有 Windows 显示器，返回物理矩形和 DPI 信息。

    Returns:
        列表，每项为 ((left, top, right, bottom), dpi) 元组。
        坐标为 Windows 物理像素坐标，dpi 为该显示器的有效 DPI。

    Note:
        使用 EnumDisplayMonitors + GetDpiForMonitor API，
        在 Windows 8.1+ 上可正确获取每个显示器独立的 DPI。
    """
    import ctypes
    from ctypes import wintypes

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG), ("top", wintypes.LONG),
            ("right", wintypes.LONG), ("bottom", wintypes.LONG)
        ]

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", RECT),
            ("rcWork", RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32)
        ]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    monitors: list[tuple[tuple[int, int, int, int], int]] = []

    def callback(hMonitor, hdcMonitor, lprcMonitor, dwData):  # noqa: ARG001
        """EnumDisplayMonitors 回调：收集每个显示器的矩形和 DPI。"""
        mi = MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if not user32.GetMonitorInfoW(hMonitor, ctypes.byref(mi)):
            return True  # 跳过获取失败的显示器，继续枚举

        rect = (mi.rcMonitor.left, mi.rcMonitor.top, mi.rcMonitor.right, mi.rcMonitor.bottom)

        # 获取显示器 DPI (Windows 8.1+ API)
        dpi = 96  # 默认标准 DPI
        try:
            shcore = ctypes.WinDLL("shcore", use_last_error=True)
            dpiX = wintypes.UINT()
            dpiY = wintypes.UINT()
            # GetDpiForMonitor: MDT_EFFECTIVE_DPI = 0
            if shcore.GetDpiForMonitor(hMonitor, 0, ctypes.byref(dpiX), ctypes.byref(dpiY)) == 0:
                dpi = int(dpiX.value) if dpiX.value > 0 else 96
        except (OSError, AttributeError):
            pass  # Windows 7 或 API 不可用，使用默认值

        monitors.append((rect, dpi))
        return True

    MONITORENUMPROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
        ctypes.POINTER(RECT), wintypes.LPARAM
    )
    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(callback), 0)
    return monitors


def _build_monitor_to_screen_mapping() -> dict[tuple[int, int], tuple[QtGui.QScreen, int, tuple[int, int, int, int]]]:
    """构建 Windows 显示器物理坐标到 Qt 屏幕的映射。

    核心思路:
        Windows API 和 Qt 对多显示器的坐标表示可能不同（尤其在混合 DPI 环境下）。
        本函数将两者按位置排序后一一对应，确保即使坐标系数值不同，
        只要相对位置关系一致就能正确匹配。

    Returns:
        字典 {(mon_left, mon_top): (qt_screen, mon_dpi, mon_rect), ...}
        - (mon_left, mon_top): Windows 显示器左上角物理坐标，用作索引键
        - qt_screen: 对应的 Qt QScreen 对象
        - mon_dpi: 该显示器的 DPI
        - mon_rect: 完整的物理矩形 (left, top, right, bottom)

    Note:
        如果显示器数量和 Qt 屏幕数量不匹配，多余的显示器将被忽略。
    """
    monitors = _enumerate_all_monitors()
    screens = list(QtGui.QGuiApplication.screens())

    if not monitors or not screens:
        return {}

    # 按位置排序（先按 Y，再按 X），保证相对位置匹配
    monitors_sorted = sorted(monitors, key=lambda m: (m[0][1], m[0][0]))
    screens_sorted = sorted(screens, key=lambda s: (s.geometry().y(), s.geometry().x()))

    mapping: dict[tuple[int, int], tuple[QtGui.QScreen, int, tuple[int, int, int, int]]] = {}

    # 一一对应（取两者较小的数量）
    for i, (mon_rect, mon_dpi) in enumerate(monitors_sorted):
        if i >= len(screens_sorted):
            break
        qt_screen = screens_sorted[i]
        mon_left, mon_top = mon_rect[0], mon_rect[1]
        mapping[(mon_left, mon_top)] = (qt_screen, mon_dpi, mon_rect)

    return mapping


# 显示器映射缓存（避免每次调用都重新计算）
# 注意：在多线程环境下，此缓存可能存在竞态条件，但对于 UI 线程使用场景是安全的
_monitor_mapping_cache: dict | None = None


def _get_monitor_mapping() -> dict[tuple[int, int], tuple[QtGui.QScreen, int, tuple[int, int, int, int]]]:
    """获取显示器映射（带缓存）。

    Returns:
        显示器坐标到 Qt 屏幕的映射字典，详见 `_build_monitor_to_screen_mapping`。
    """
    global _monitor_mapping_cache
    if _monitor_mapping_cache is None:
        _monitor_mapping_cache = _build_monitor_to_screen_mapping()
    return _monitor_mapping_cache


def _invalidate_monitor_cache() -> None:
    """使显示器映射缓存失效。

    应在以下情况调用:
        - 显示器配置改变（接入/拔出显示器）
        - DPI 设置改变
        - 每次开始区域选择前（确保使用最新配置）
    """
    global _monitor_mapping_cache
    _monitor_mapping_cache = None


def _physical_rect_to_screen_local(
    phys_left: int, phys_top: int, phys_right: int, phys_bottom: int
) -> tuple[QtCore.QRect, QtGui.QScreen | None]:
    """将 Windows 物理像素坐标转换为 Qt 全局逻辑坐标。

    转换流程:
        1. 根据矩形中心点确定所在的 Windows 显示器
        2. 计算矩形相对于该显示器左上角的偏移
        3. 按显示器 DPI 缩放为逻辑像素
        4. 加上对应 Qt 屏幕的 geometry 偏移，得到 Qt 全局坐标

    Args:
        phys_left: 物理像素左边界
        phys_top: 物理像素上边界
        phys_right: 物理像素右边界
        phys_bottom: 物理像素下边界

    Returns:
        (qt_rect, target_screen) 元组:
        - qt_rect: Qt 全局逻辑坐标下的矩形，可直接用于 QWidget.setGeometry()
        - target_screen: 矩形所在的 Qt 屏幕对象，用于创建只覆盖该屏幕的 overlay

    Note:
        此函数会在每次调用时刷新显示器缓存，确保获取最新的显示器配置。
        这样可以正确处理动态接入/拔出显示器的情况。
    """
    # 刷新缓存以获取最新的显示器配置
    _invalidate_monitor_cache()
    mapping = _get_monitor_mapping()

    # 使用窗口中心点判断所在显示器（避免窗口跨屏幕时的歧义）
    center_x = (phys_left + phys_right) // 2
    center_y = (phys_top + phys_bottom) // 2

    target_screen: QtGui.QScreen | None = None
    target_dpi = 96
    target_mon_rect: tuple[int, int, int, int] | None = None

    # 查找包含中心点的显示器
    for (mon_left, mon_top), (qt_screen, mon_dpi, mon_rect) in mapping.items():
        mon_l, mon_t, mon_r, mon_b = mon_rect
        if mon_l <= center_x <= mon_r and mon_t <= center_y <= mon_b:
            target_screen = qt_screen
            target_dpi = mon_dpi
            target_mon_rect = mon_rect
            break

    # 回退策略：如果找不到对应显示器，使用主屏幕
    if target_screen is None or target_mon_rect is None:
        target_screen = QtGui.QGuiApplication.primaryScreen()
        if target_screen is None:
            # 极端情况：没有可用屏幕，返回占位矩形
            return (QtCore.QRect(0, 0, 100, 100), None)

        target_dpi = int(target_screen.devicePixelRatio() * 96)
        geom = target_screen.geometry()
        ratio = target_screen.devicePixelRatio()
        target_mon_rect = (
            0, 0,
            int(geom.width() * ratio),
            int(geom.height() * ratio)
        )

    # DPI 缩放因子：物理像素 -> 逻辑像素
    scale = target_dpi / 96.0 if target_dpi > 0 else 1.0
    mon_l, mon_t = target_mon_rect[0], target_mon_rect[1]

    # 计算相对于显示器物理起点的偏移，并缩放为逻辑坐标
    local_left = int((phys_left - mon_l) / scale)
    local_top = int((phys_top - mon_t) / scale)
    local_right = int((phys_right - mon_l) / scale)
    local_bottom = int((phys_bottom - mon_t) / scale)

    # 加上 Qt 屏幕的 geometry 偏移，得到 Qt 全局坐标
    qt_geom = target_screen.geometry()
    global_left = qt_geom.x() + local_left
    global_top = qt_geom.y() + local_top
    global_right = qt_geom.x() + local_right
    global_bottom = qt_geom.y() + local_bottom

    result_rect = QtCore.QRect(
        global_left, global_top,
        global_right - global_left,
        global_bottom - global_top
    )
    return (result_rect, target_screen)


class MainWindow(QtWidgets.QWidget):
    """OCR 监控应用的主窗口。

    功能概述:
        - 选择目标窗口和监控区域（支持窗口被遮挡）
        - 实时 OCR 扫描和结果展示
        - 警报状态可视化（呼吸灯动画、状态指示器）
        - 调试预览和日志输出

    工作流程:
        1. 用户点击"选择区域"按钮，选择目标窗口
        2. 在目标窗口上拖拽框选监控区域
        3. 点击"开始监控"，后台 Worker 线程定时抓取并 OCR
        4. 检测到异常内容时触发警报显示

    Attributes:
        worker (Worker): 后台 OCR 工作线程
        ort: ONNX Runtime 会话（可选，用于 GPU 加速）
        readers: OCR 引擎缓存字典
    """

    def __init__(self, *, ort: Optional[Any] = None) -> None:
        """初始化主窗口。

        Args:
            ort: 可选的 ONNX Runtime 会话，传入后可启用 GPU 加速。
                 如果为 None，将使用 CPU 推理。
        """
        super().__init__()
        self.setObjectName("MainWindow")
        self.setWindowTitle("敌对中立监控程序")

        # 设置初始大小和最小大小，允许用户自由调整窗口尺寸
        self.resize(560, 800)
        self.setMinimumSize(400, 600)

        # ONNX Runtime 会话（用于 OCR 加速）
        self.ort = ort
        # 当前屏幕的设备像素比（用于 DPI 感知）
        self.screen_ratio = self.devicePixelRatio()
        # OCR 引擎缓存：{"CPU": engine_instance, ...}
        self.readers: dict[str, Any] = {"CPU": None}

        # 后台工作线程
        self.worker = Worker()
        self.worker.result_ready.connect(self.update_ui)

        # ------------------------------------
        # 目标窗口和区域状态
        # ------------------------------------
        # 窗口选择模式：先选窗口(hwnd)，再在窗口内拖拽选区域
        # 区域使用归一化坐标 (0~1) 存储，与 DPI 和窗口大小解耦
        self._target_hwnd: Optional[int] = None
        self._target_window_title: str = ""
        self._target_window_rect_global: Optional[QtCore.QRect] = None

        # ------------------------------------
        # 动画和视觉效果
        # ------------------------------------
        self.breathing_animation: Optional[QtCore.QPropertyAnimation] = None
        self.breathing_opacity = 1.0
        self._monitor_glow_effect: Optional[QtWidgets.QGraphicsDropShadowEffect] = None

        # 当前预览图像缓存（用于窗口缩放时重新渲染）
        self.current_preview_image: Optional[QtGui.QImage] = None

        # 初始化 UI 组件和样式
        self.init_ui()
        self.apply_styles()

        # Worker 线程日志输出到 UI
        self.worker.log_ready.connect(self.log_output.appendPlainText)

    def init_ui(self) -> None:
        """初始化所有 UI 组件。

        创建以下区域:
            - 顶部标题栏（应用标题 + 状态胶囊）
            - 核心数值显示卡片（扫描结果 + 置信度）
            - 实时预览卡片（调试用截图预览）
            - 控制面板卡片（选择区域/调试快照/开始监控按钮）
            - 系统日志卡片（日志输出区域）
        """

        def add_shadow(w: QtWidgets.QWidget, blur: int = 28, y: int = 10, alpha: int = 26) -> None:
            """为控件添加阴影效果，增强卡片立体感。"""
            eff = QtWidgets.QGraphicsDropShadowEffect(w)
            eff.setBlurRadius(blur)
            eff.setOffset(0, y)
            eff.setColor(QtGui.QColor(0, 0, 0, alpha))
            w.setGraphicsEffect(eff)

        # 主布局
        self.main_layout = QtWidgets.QVBoxLayout(self)
        self.main_layout.setSpacing(14)
        self.main_layout.setContentsMargins(24, 20, 24, 20)

        # 0. 顶部标题栏
        header = QtWidgets.QFrame()
        header.setObjectName("HeaderBar")
        header_layout = QtWidgets.QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(12)

        title_col = QtWidgets.QVBoxLayout()
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(2)

        app_title = QtWidgets.QLabel("敌对中立监控")
        app_title.setObjectName("AppTitle")
        app_sub = QtWidgets.QLabel("OCR 区域监控 · 简约清新主题")
        app_sub.setObjectName("AppSubtitle")

        title_col.addWidget(app_title)
        title_col.addWidget(app_sub)

        header_layout.addLayout(title_col, 1)

        self.top_pill = QtWidgets.QLabel("就绪")
        self.top_pill.setObjectName("StatusPill")
        self.top_pill.setProperty("tone", "neutral")
        header_layout.addWidget(
            self.top_pill,
            0,
            QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter,
        )

        self.main_layout.addWidget(header)

        # 1. 核心数值显示区 (大卡片)
        self.display_card = QtWidgets.QFrame()
        self.display_card.setObjectName("DisplayCard")
        display_layout = QtWidgets.QVBoxLayout(self.display_card)
        display_layout.setContentsMargins(18, 16, 18, 16)
        display_layout.setSpacing(6)

        self.status_title = QtWidgets.QLabel("系统就绪")
        self.status_title.setObjectName("StatusTitle")
        self.status_title.setProperty("alert", "false")
        self.status_title.setProperty("scanning", "false")
        self.status_title.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        self.result_display = QtWidgets.QLabel("待机")
        self.result_display.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.result_display.setObjectName("BigNumber")

        self.conf_label = QtWidgets.QLabel("平均置信度 --")
        self.conf_label.setObjectName("MetaText")
        self.conf_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        display_layout.addWidget(self.status_title)
        display_layout.addWidget(self.result_display)
        display_layout.addWidget(self.conf_label)
        add_shadow(self.display_card)
        self.main_layout.addWidget(self.display_card)

        # 2. 实时预览区（卡片）
        self.preview_card = QtWidgets.QFrame()
        self.preview_card.setObjectName("Card")
        preview_card_layout = QtWidgets.QVBoxLayout(self.preview_card)
        preview_card_layout.setContentsMargins(16, 14, 16, 16)
        preview_card_layout.setSpacing(10)

        preview_head = QtWidgets.QHBoxLayout()
        preview_head.setContentsMargins(0, 0, 0, 0)
        preview_head.setSpacing(10)

        preview_title = QtWidgets.QLabel("实时预览")
        preview_title.setObjectName("CardTitle")
        preview_hint = QtWidgets.QLabel("仅用于调试")
        preview_hint.setObjectName("CardHint")

        title_wrap = QtWidgets.QVBoxLayout()
        title_wrap.setContentsMargins(0, 0, 0, 0)
        title_wrap.setSpacing(1)
        title_wrap.addWidget(preview_title)
        title_wrap.addWidget(preview_hint)

        preview_head.addLayout(title_wrap, 1)

        # 预览开关按钮
        self.debug_btn = QtWidgets.QPushButton("开启")
        self.debug_btn.setCheckable(True)
        self.debug_btn.setFixedHeight(34)
        self.debug_btn.setMinimumWidth(96)
        self.debug_btn.setObjectName("GhostToggle")
        preview_head.addWidget(
            self.debug_btn,
            0,
            QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter,
        )

        preview_card_layout.addLayout(preview_head)

        self.preview_label = QtWidgets.QLabel("实时流已限制")
        # 减小最小尺寸，防止在小窗口下预览区无法缩放导致显示不全（原为 360, 220）
        self.preview_label.setMinimumSize(100, 60)
        self.preview_label.setObjectName("PreviewWindow")
        self.preview_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setScaledContents(False)
        # 设置大小策略：水平和垂直都可扩展
        self.preview_label.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        preview_card_layout.addWidget(self.preview_label, 1)
        add_shadow(self.preview_card)
        self.main_layout.addWidget(self.preview_card, 1)  # 添加拉伸因子

        # 3. 操作按钮区（卡片）
        self.action_card = QtWidgets.QFrame()
        self.action_card.setObjectName("Card")
        action_layout = QtWidgets.QVBoxLayout(self.action_card)
        action_layout.setContentsMargins(16, 14, 16, 16)
        action_layout.setSpacing(10)

        action_title = QtWidgets.QLabel("控制面板")
        action_title.setObjectName("CardTitle")
        action_sub = QtWidgets.QLabel("先选择窗口并框选区域，再开始监控（窗口可被遮挡）")
        action_sub.setObjectName("CardHint")
        action_layout.addWidget(action_title)
        action_layout.addWidget(action_sub)

        btn_grid = QtWidgets.QGridLayout()
        btn_grid.setSpacing(10)

        self.select_btn = QtWidgets.QPushButton("选择区域")
        self.select_btn.setFixedHeight(42)

        self.print_btn = QtWidgets.QPushButton("调试快照")
        self.print_btn.setFixedHeight(42)

        self.monitor_btn = QtWidgets.QPushButton("开始监控")
        self.monitor_btn.setObjectName("PrimaryBtn")
        self.monitor_btn.setFixedHeight(50)
        self.monitor_btn.setProperty("state", "idle")
        # 注意：不在这里给按钮上 OpacityEffect（某些环境会导致按钮完全透明）

        btn_grid.addWidget(self.select_btn, 0, 0)
        btn_grid.addWidget(self.print_btn, 0, 1)
        btn_grid.addWidget(self.monitor_btn, 1, 0, 1, 2)  # 跨两列
        action_layout.addLayout(btn_grid)
        add_shadow(self.action_card)
        self.main_layout.addWidget(self.action_card)

        # 4. 日志区（卡片）
        self.log_card = QtWidgets.QFrame()
        self.log_card.setObjectName("Card")
        log_layout = QtWidgets.QVBoxLayout(self.log_card)
        log_layout.setContentsMargins(16, 14, 16, 16)
        log_layout.setSpacing(10)

        log_title = QtWidgets.QLabel("系统日志")
        log_title.setObjectName("CardTitle")
        log_layout.addWidget(log_title)

        self.log_output = QtWidgets.QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setPlaceholderText("系统日志将显示在此...")
        self.log_output.setMinimumHeight(100)
        self.log_output.setMaximumHeight(220)
        # 设置大小策略：可以垂直扩展
        self.log_output.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        log_layout.addWidget(self.log_output)
        add_shadow(self.log_card)
        self.main_layout.addWidget(self.log_card)

        # 绑定事件
        self.select_btn.clicked.connect(self.start_selection)
        self.monitor_btn.clicked.connect(self.toggle_monitoring)
        self.print_btn.clicked.connect(self.manual_debug_print)
        self.debug_btn.toggled.connect(self.on_debug_toggled)

    def on_debug_toggled(self, checked: bool) -> None:
        """处理调试预览开关切换。

        Args:
            checked: True 表示开启预览，False 表示关闭。
        """
        self.debug_btn.setText("关闭" if checked else "开启")
        self.worker.show_debug = bool(checked)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        """处理窗口大小改变事件。

        当窗口大小改变时，如果有预览图片且预览功能开启，
        会重新缩放图片以适应新的预览区域大小。
        """
        super().resizeEvent(event)
        # 仅在有预览图片且预览开启时才重新渲染
        if self.current_preview_image is not None and self.debug_btn.isChecked():
            self.preview_label.setPixmap(
                QtGui.QPixmap.fromImage(self.current_preview_image).scaled(
                    self.preview_label.size(),
                    QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                    QtCore.Qt.TransformationMode.SmoothTransformation,
                )
            )

    def apply_styles(self) -> None:
        """应用主题样式表。

        使用现代化简约清新主题:
            - 浅色背景 + 卡片化布局
            - 低饱和度强调色（teal/红色警报）
            - 圆角边框 + 轻微阴影
        """
        self.setStyleSheet(MAIN_STYLESHEET)

    def get_reader(self) -> Any:
        """获取或延迟初始化 OCR 引擎。

        使用懒加载模式：首次调用时才加载模型，避免启动时的长时间等待。
        加载过程中会显示等待光标，并在日志中输出状态。

        Returns:
            RapidOCR 引擎实例，失败时返回 None。

        Note:
            当前仅支持 CPU 模式，未来可扩展为 GPU/NPU 等多种后端。
        """
        m_key = "CPU"
        if self.readers[m_key] is None:
            self.log_output.appendPlainText("⏳ 正在加载 RapidOCR 引擎...")
            QtWidgets.QApplication.setOverrideCursor(
                QtGui.QCursor(QtCore.Qt.CursorShape.WaitCursor)
            )
            try:
                engine = create_rapidocr_engine(
                    ort=self.ort,
                    log=self.log_output.appendPlainText,
                )
                self.readers[m_key] = engine
            finally:
                QtWidgets.QApplication.restoreOverrideCursor()
        return self.readers[m_key]

    def start_selection(self) -> None:
        """启动区域选择流程。

        流程:
            1. 弹出窗口选择器让用户选择目标窗口
            2. 验证窗口有效性，尝试将其置前
            3. 获取窗口物理坐标并转换为 Qt 逻辑坐标
            4. 创建半透明覆盖层，让用户在窗口范围内拖拽框选区域

        Note:
            - 选择的窗口即使被遮挡，后续监控也能正常工作（使用 PrintWindow API）
            - 区域坐标会转换为归一化坐标存储，与窗口大小/DPI 解耦
        """
        # Step 1: 让用户从窗口列表中选择目标窗口
        win = pick_window(self)
        if win is None:
            return  # 用户取消选择

        hwnd = int(win.hwnd)
        # 获取窗口标题，多重回退确保有可识别的名称
        title = (win.title or "").strip() or get_window_title(hwnd) or f"0x{hwnd:08X}"

        # Step 2: 验证窗口仍然存在
        if not is_window(hwnd):
            self.log_output.appendPlainText("⚠️ 目标窗口不存在或已关闭，请重新选择。")
            return

        self._target_hwnd = hwnd
        self._target_window_title = title
        self.log_output.appendPlainText(f"🪟 已选择窗口：{title}")

        # 尽力将目标窗口置前，方便用户看到并框选（后续监控不要求置前）
        activate_window(hwnd)

        # Step 3: 获取窗口物理像素坐标
        (phys_l, phys_t, phys_r, phys_b), used_dwm = get_window_rect_ltrb(hwnd)

        # 调试输出：坐标转换信息（帮助排查多屏幕/高 DPI 问题）
        self._log_coordinate_debug_info(phys_l, phys_t, phys_r, phys_b, used_dwm)

        # Step 4: 将物理坐标转换为 Qt 逻辑坐标
        allowed, target_screen = _physical_rect_to_screen_local(phys_l, phys_t, phys_r, phys_b)
        self.log_output.appendPlainText(
            f"   转换后Qt坐标: ({allowed.x()}, {allowed.y()}) - "
            f"({allowed.x() + allowed.width()}, {allowed.y() + allowed.height()})"
        )
        if target_screen:
            self.log_output.appendPlainText(f"   目标屏幕: {target_screen.name()}")

        # 确保矩形尺寸有效（防止零宽/零高导致后续异常）
        if allowed.width() < 1:
            allowed.setWidth(1)
        if allowed.height() < 1:
            allowed.setHeight(1)

        self._target_window_rect_global = allowed

        # Step 5: 创建区域选择覆盖层
        hint = f"窗口：{title}\n在该窗口内拖拽选择监控区域（ESC 取消）"
        # 只覆盖目标屏幕，避免多屏幕虚拟桌面的坐标空隙问题
        self.cw = AreaSelectionOverlay(
            allowed_rect=allowed,
            hint_text=hint,
            target_screen=target_screen
        )
        self.cw.selection_made.connect(self.on_area)
        self.cw.show()

    def _log_coordinate_debug_info(
        self, phys_l: int, phys_t: int, phys_r: int, phys_b: int, used_dwm: bool
    ) -> None:
        """输出坐标转换调试信息到日志。

        帮助排查多屏幕/高 DPI 环境下的坐标对齐问题。
        """
        self.log_output.appendPlainText("🔍 调试信息:")
        self.log_output.appendPlainText(
            f"   物理坐标: ({phys_l}, {phys_t}) - ({phys_r}, {phys_b}) [DWM={used_dwm}]"
        )

        # 打印所有 Qt 屏幕信息
        for i, screen in enumerate(QtGui.QGuiApplication.screens()):
            geom = screen.geometry()
            ratio = screen.devicePixelRatio()
            self.log_output.appendPlainText(
                f"   Qt屏幕[{i}]: geometry=({geom.x()},{geom.y()},"
                f"{geom.width()}x{geom.height()}), ratio={ratio}"
            )

        # 打印 Windows 显示器信息
        monitors = _enumerate_all_monitors()
        for i, (mon_rect, dpi) in enumerate(monitors):
            self.log_output.appendPlainText(
                f"   Win显示器[{i}]: rect={mon_rect}, DPI={dpi}"
            )

    def on_area(self, r: QtCore.QRect) -> None:
        """处理区域选择完成事件。

        将用户选择的 Qt 逻辑坐标矩形转换为工作线程可用的格式:
            - 窗口模式: 转换为归一化坐标 (0~1)，与 DPI 和窗口大小解耦
            - 屏幕模式 (兜底): 转换为物理像素坐标

        Args:
            r: 用户选择的矩形，Qt 全局逻辑坐标。

        Note:
            归一化坐标的优势:
            - 窗口移动/缩放后仍能正确定位
            - 跨不同 DPI 屏幕时无需重新选择
            - 窗口被遮挡时通过 PrintWindow 抓取也能对齐
        """
        # 模式一：窗口模式（推荐）
        if self._target_hwnd is not None and self._target_window_rect_global is not None:
            allowed = self._target_window_rect_global

            # 将选区裁剪到窗口范围内
            rr = r.intersected(allowed)
            if rr.isNull() or rr.width() <= 5 or rr.height() <= 5:
                self.log_output.appendPlainText("⚠️ 选择区域太小，请重试。")
                return

            # 计算归一化坐标 (0~1)
            # 使用 width()/height() 而非 right()/bottom()，避免 QRect 包含语义的 1px 误差
            ax, ay = allowed.x(), allowed.y()
            aw, ah = max(1, allowed.width()), max(1, allowed.height())

            x1 = float(rr.x() - ax) / float(aw)
            y1 = float(rr.y() - ay) / float(ah)
            x2 = float(rr.x() + rr.width() - ax) / float(aw)
            y2 = float(rr.y() + rr.height() - ay) / float(ah)

            # Clamp 到 [0, 1] 范围，防止浮点误差导致越界
            x1 = max(0.0, min(1.0, x1))
            y1 = max(0.0, min(1.0, y1))
            x2 = max(0.0, min(1.0, x2))
            y2 = max(0.0, min(1.0, y2))

            # 二次验证：归一化后的区域仍需足够大
            if x2 - x1 <= 0.002 or y2 - y1 <= 0.002:
                self.log_output.appendPlainText("⚠️ 选择区域太小，请重试。")
                return

            # 设置 Worker 的目标参数
            self.worker.target_hwnd = int(self._target_hwnd)
            self.worker.target_norm_rect = (x1, y1, x2, y2)
            self.worker.target_rect = None  # 清除旧的屏幕模式参数

            self.log_output.appendPlainText(
                f"🎯 已选择窗口区域：{self._target_window_title}  "
                f"({(x2 - x1):.1%} x {(y2 - y1):.1%})"
            )
            return

        # 模式二：屏幕模式（兜底，用于没有选择特定窗口的情况）
        ratio = self.screen_ratio
        # 添加 8px 边距，容错鼠标精度误差
        x1 = max(0, int(r.x() * ratio) - 8)
        y1 = max(0, int(r.y() * ratio) - 8)
        x2 = int((r.x() + r.width()) * ratio) + 8
        y2 = int((r.y() + r.height()) * ratio) + 8

        self.worker.target_rect = (x1, y1, x2, y2)
        self.worker.target_hwnd = None
        self.worker.target_norm_rect = None

        self.log_output.appendPlainText(f"🎯 已选择区域：{x2 - x1}x{y2 - y1} 像素")

    def toggle_monitoring(self) -> None:
        """切换监控状态（开始/停止）。

        开始监控时:
            - 初始化 OCR 引擎（首次）
            - 启动后台工作线程
            - 更新 UI 状态（按钮文字、状态指示器、呼吸灯动画）

        停止监控时:
            - 通知工作线程停止
            - 重置 UI 到就绪状态
        """
        if not self.worker.is_running:
            # === 开始监控 ===
            reader = self.get_reader()
            if not reader:
                return  # OCR 引擎加载失败

            self.worker.reader = reader
            self.worker.is_running = True
            self.worker.start()

            # 更新按钮状态
            self.monitor_btn.setText("停止监控")
            self.monitor_btn.setProperty("state", "running")
            self._refresh_widget_style(self.monitor_btn)

            # 更新状态胶囊
            self.top_pill.setText("扫描中")
            self.top_pill.setProperty("tone", "info")
            self._refresh_widget_style(self.top_pill)

            # 启动呼吸灯动画
            self.start_breathing_animation()
        else:
            # === 停止监控 ===
            self.worker.stop()

            # 恢复按钮状态
            self.monitor_btn.setText("开始监控")
            self.monitor_btn.setProperty("state", "idle")
            self._refresh_widget_style(self.monitor_btn)

            # 恢复状态胶囊
            self.top_pill.setText("就绪")
            self.top_pill.setProperty("tone", "neutral")
            self._refresh_widget_style(self.top_pill)

            # 停止呼吸灯动画
            self.stop_breathing_animation()

    def _refresh_widget_style(self, widget: QtWidgets.QWidget) -> None:
        """刷新控件样式（在动态修改属性后调用）。

        Qt 的属性选择器不会自动响应 setProperty() 的改变，
        需要手动 unpolish + polish 来触发样式重新计算。
        """
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def start_breathing_animation(self) -> None:
        """启动监控按钮的呼吸灯动画。

        使用外发光（DropShadow）实现呼吸效果，避免影响按钮本体的可见性。
        动画通过改变模糊半径实现"呼吸"视觉效果。

        Note:
            - 正常扫描状态使用 teal 色发光
            - 报警状态使用红色发光（通过 state 属性判断）
        """
        # 创建外发光效果（如果尚未创建）
        if self._monitor_glow_effect is None:
            eff = QtWidgets.QGraphicsDropShadowEffect(self.monitor_btn)
            eff.setOffset(0, 0)
            eff.setBlurRadius(22)
            eff.setColor(QtGui.QColor(20, 184, 166, 140))  # teal 色
            self._monitor_glow_effect = eff
            self.monitor_btn.setGraphicsEffect(self._monitor_glow_effect)

        # 根据当前状态设置发光颜色
        if self.monitor_btn.property("state") == "running":
            self._monitor_glow_effect.setColor(QtGui.QColor(239, 68, 68, 150))  # 红色（警报）

        # 创建模糊半径动画
        self.breathing_animation = QtCore.QPropertyAnimation(
            self._monitor_glow_effect, b"blurRadius"
        )
        self.breathing_animation.setDuration(1400)  # 一个周期 1.4 秒
        self.breathing_animation.setLoopCount(-1)   # 无限循环
        self.breathing_animation.setEasingCurve(QtCore.QEasingCurve.Type.InOutSine)

        # 关键帧：小 -> 大 -> 小
        self.breathing_animation.setKeyValueAt(0.0, 16.0)
        self.breathing_animation.setKeyValueAt(0.5, 34.0)
        self.breathing_animation.setKeyValueAt(1.0, 16.0)

        self.breathing_animation.start()

    def stop_breathing_animation(self) -> None:
        """停止呼吸灯动画并清理效果。

        停止时会移除发光效果，确保按钮在停止状态下显示正常。
        """
        if self.breathing_animation is not None:
            self.breathing_animation.stop()
            self.breathing_animation = None

        # 移除发光效果，恢复按钮正常显示
        if self._monitor_glow_effect is not None:
            self.monitor_btn.setGraphicsEffect(None)
            self._monitor_glow_effect = None

    def manual_debug_print(self) -> None:
        """手动输出调试快照到日志。

        打印最后一次 OCR 识别的原始结果，包括每个文本块的内容和置信度。
        用于调试和验证 OCR 识别效果。
        """
        res = self.worker._last_raw_results
        self.log_output.appendPlainText(
            f"\n--- Debug Snapshot ({time.strftime('%H:%M:%S')}) ---"
        )
        if not res:
            self.log_output.appendPlainText("未检测到内容。")
        else:
            for i, item in enumerate(res):
                # item 格式: (bbox, text, confidence)
                self.log_output.appendPlainText(
                    f"Block[{i}]: '{item[1]}' (conf: {item[2]:.4f})"
                )

    def update_ui(
        self,
        text: str,
        conf: float,
        qimg: QtGui.QImage,
        raw: Any  # noqa: ARG002 - 保留参数以匹配信号签名
    ) -> None:
        """处理 Worker 线程的识别结果，更新 UI 状态。

        Args:
            text: 识别到的文本内容（数字/字符串）
            conf: 平均置信度 (0~1)
            qimg: 抓取的截图（用于预览）
            raw: 原始识别结果（当前未使用，保留用于调试扩展）

        UI 更新内容:
            - 结果显示区（BigNumber）
            - 置信度标签
            - 状态标题（扫描中/警报）
            - 状态胶囊（颜色和文字）
            - 预览图片（如果开启）

        警报判定逻辑:
            - 置信度 >= 阈值 (ALARM_AVG_CONF_THRESHOLD)
            - 且文本内容不全为 "0"
        """
        # 更新结果显示
        display_text = text if text else "0"
        self.result_display.setText(display_text)
        self.conf_label.setText(
            f"平均置信度 {conf:.0%}" if conf > 0 else "平均置信度 --"
        )

        # 警报判定：置信度达标 且 内容不全为 0
        is_alert = (
            conf >= ALARM_AVG_CONF_THRESHOLD
            and bool(text)
            and any(c != "0" for c in text)
        )

        # 使用 QSS 属性选择器驱动样式变化
        # 避免直接 setStyleSheet() 覆盖全局主题
        alert_str = "true" if is_alert else "false"

        self.result_display.setProperty("alert", alert_str)
        self._refresh_widget_style(self.result_display)

        self.status_title.setProperty("alert", alert_str)
        self.status_title.setProperty("scanning", "false" if is_alert else "true")
        self._refresh_widget_style(self.status_title)

        # 更新状态指示器
        if is_alert:
            self.status_title.setText("检测到警报")
            self.top_pill.setText("警报")
            self.top_pill.setProperty("tone", "danger")
        else:
            self.status_title.setText("系统扫描中")
            if self.worker.is_running:
                self.top_pill.setText("扫描中")
                self.top_pill.setProperty("tone", "info")
            else:
                self.top_pill.setText("就绪")
                self.top_pill.setProperty("tone", "neutral")

        self._refresh_widget_style(self.top_pill)

        # 缓存预览图片（用于窗口缩放时重新渲染）
        self.current_preview_image = qimg

        # 仅在预览开启时更新图片（节省性能）
        if self.debug_btn.isChecked():
            self.preview_label.setPixmap(
                QtGui.QPixmap.fromImage(qimg).scaled(
                    self.preview_label.size(),
                    QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                    QtCore.Qt.TransformationMode.SmoothTransformation,
                )
            )

