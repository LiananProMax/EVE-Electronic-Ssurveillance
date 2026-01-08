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
from ..win.window_api import activate_window, get_window_rect_dips, get_window_title, is_window


class MainWindow(QtWidgets.QWidget):
    def __init__(self, *, ort: Optional[Any] = None) -> None:
        super().__init__()
        self.setObjectName("MainWindow")
        self.setWindowTitle("敌对中立监控程序")
        # 设置初始大小和最小大小，但允许用户调整窗口
        self.resize(560, 800)
        self.setMinimumSize(400, 600)

        self.ort = ort
        self.screen_ratio = self.devicePixelRatio()
        self.readers = {"CPU": None}

        self.worker = Worker()
        self.worker.result_ready.connect(self.update_ui)

        # 窗口选择模式：先选窗口(hwnd)，再在窗口内拖拽选区域（区域使用归一化坐标存储）
        self._target_hwnd: Optional[int] = None
        self._target_window_title: str = ""
        self._target_window_rect_global: Optional[QtCore.QRect] = None

        # 呼吸灯动画
        self.breathing_animation = None
        self.breathing_opacity = 1.0
        self._monitor_glow_effect = None

        # 保存当前预览图像，用于窗口缩放时重新渲染
        self.current_preview_image = None

        self.init_ui()
        self.apply_styles()
        # Worker 线程日志输出到 UI
        self.worker.log_ready.connect(self.log_output.appendPlainText)

    def init_ui(self) -> None:
        def add_shadow(w: QtWidgets.QWidget, blur: int = 28, y: int = 10, alpha: int = 26) -> None:
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
        self.preview_label.setMinimumSize(360, 220)
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
        self.debug_btn.setText("关闭" if checked else "开启")
        self.worker.show_debug = bool(checked)

    def resizeEvent(self, event) -> None:
        """窗口大小改变时的处理"""
        super().resizeEvent(event)
        # 如果有预览图片，重新缩放以适应新的窗口大小
        if hasattr(self, "current_preview_image") and self.current_preview_image is not None:
            if self.debug_btn.isChecked():
                self.preview_label.setPixmap(
                    QtGui.QPixmap.fromImage(self.current_preview_image).scaled(
                        self.preview_label.size(),
                        QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                        QtCore.Qt.TransformationMode.SmoothTransformation,
                    )
                )

    def apply_styles(self) -> None:
        """应用现代化简约清新主题样式（浅色/卡片化/低饱和强调色）"""
        self.setStyleSheet(MAIN_STYLESHEET)

    def get_reader(self):
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
        # 新流程：先选择窗口，再在窗口内框选（窗口被遮挡也能继续监控）
        win = pick_window(self)
        if win is None:
            return

        hwnd = int(win.hwnd)
        title = (win.title or "").strip() or get_window_title(hwnd) or f"0x{hwnd:08X}"
        if not is_window(hwnd):
            self.log_output.appendPlainText("⚠️ 目标窗口不存在或已关闭，请重新选择。")
            return

        self._target_hwnd = hwnd
        self._target_window_title = title
        self.log_output.appendPlainText(f"🪟 已选择窗口：{title}")

        # 尽力置前方便用户框选；后续监控不要求置前
        activate_window(hwnd)

        l, t, r, b = get_window_rect_dips(hwnd)
        allowed = QtCore.QRect(int(l), int(t), max(1, int(r - l)), max(1, int(b - t)))
        self._target_window_rect_global = allowed

        hint = f"窗口：{title}\n在该窗口内拖拽选择监控区域（ESC 取消）"
        self.cw = AreaSelectionOverlay(allowed_rect=allowed, hint_text=hint)
        self.cw.selection_made.connect(self.on_area)
        self.cw.show()

    def on_area(self, r: QtCore.QRect) -> None:
        # 1) 窗口模式：将选区转成相对窗口的归一化坐标（0~1），避免 DPI/遮挡问题
        if self._target_hwnd is not None and self._target_window_rect_global is not None:
            allowed = self._target_window_rect_global
            rr = r.intersected(allowed)
            if rr.isNull() or rr.width() <= 5 or rr.height() <= 5:
                self.log_output.appendPlainText("⚠️ 选择区域太小，请重试。")
                return

            # 使用右下角“开区间”端点，避免 QRect.right 的包含语义带来 1px 误差
            ax, ay, aw, ah = allowed.x(), allowed.y(), max(1, allowed.width()), max(1, allowed.height())
            x1 = float(rr.x() - ax) / float(aw)
            y1 = float(rr.y() - ay) / float(ah)
            x2 = float(rr.x() + rr.width() - ax) / float(aw)
            y2 = float(rr.y() + rr.height() - ay) / float(ah)

            # clamp
            x1 = min(max(x1, 0.0), 1.0)
            y1 = min(max(y1, 0.0), 1.0)
            x2 = min(max(x2, 0.0), 1.0)
            y2 = min(max(y2, 0.0), 1.0)
            if x2 - x1 <= 0.002 or y2 - y1 <= 0.002:
                self.log_output.appendPlainText("⚠️ 选择区域太小，请重试。")
                return

            self.worker.target_hwnd = int(self._target_hwnd)
            self.worker.target_norm_rect = (x1, y1, x2, y2)
            # 关闭旧的屏幕区域模式，避免混淆
            self.worker.target_rect = None

            self.log_output.appendPlainText(
                f"🎯 已选择窗口区域：{self._target_window_title}  ({(x2 - x1):.1%} x {(y2 - y1):.1%})"
            )
            return

        # 2) 旧屏幕模式（兜底）：全屏框选 -> 屏幕像素坐标
        ratio = self.screen_ratio
        x1, y1 = max(0, int(r.x() * ratio) - 8), max(0, int(r.y() * ratio) - 8)
        x2, y2 = int((r.x() + r.width()) * ratio) + 8, int((r.y() + r.height()) * ratio) + 8
        self.worker.target_rect = (x1, y1, x2, y2)
        self.worker.target_hwnd = None
        self.worker.target_norm_rect = None
        self.log_output.appendPlainText(f"🎯 已选择区域：{x2 - x1}x{y2 - y1} 像素")

    def toggle_monitoring(self) -> None:
        if not self.worker.is_running:
            reader = self.get_reader()
            if not reader:
                return
            self.worker.reader = reader
            self.worker.is_running = True
            self.worker.start()

            self.monitor_btn.setText("停止监控")
            self.monitor_btn.setProperty("state", "running")
            self.monitor_btn.style().unpolish(self.monitor_btn)
            self.monitor_btn.style().polish(self.monitor_btn)

            self.top_pill.setText("扫描中")
            self.top_pill.setProperty("tone", "info")
            self.top_pill.style().unpolish(self.top_pill)
            self.top_pill.style().polish(self.top_pill)

            self.start_breathing_animation()
        else:
            self.worker.stop()

            self.monitor_btn.setText("开始监控")
            self.monitor_btn.setProperty("state", "idle")
            self.monitor_btn.style().unpolish(self.monitor_btn)
            self.monitor_btn.style().polish(self.monitor_btn)

            self.top_pill.setText("就绪")
            self.top_pill.setProperty("tone", "neutral")
            self.top_pill.style().unpolish(self.top_pill)
            self.top_pill.style().polish(self.top_pill)
            self.stop_breathing_animation()

    def start_breathing_animation(self) -> None:
        """启动呼吸灯动画"""
        # 改为“外发光”呼吸：不会影响按钮本体可见性
        if self._monitor_glow_effect is None:
            eff = QtWidgets.QGraphicsDropShadowEffect(self.monitor_btn)
            eff.setOffset(0, 0)
            eff.setBlurRadius(22)
            eff.setColor(QtGui.QColor(20, 184, 166, 140))  # teal
            self._monitor_glow_effect = eff
            self.monitor_btn.setGraphicsEffect(self._monitor_glow_effect)

        # 如果正在报警态（红色），发光也用红色系
        if self.monitor_btn.property("state") == "running":
            self._monitor_glow_effect.setColor(QtGui.QColor(239, 68, 68, 150))

        self.breathing_animation = QtCore.QPropertyAnimation(
            self._monitor_glow_effect, b"blurRadius"
        )
        self.breathing_animation.setDuration(1400)
        self.breathing_animation.setLoopCount(-1)  # 无限循环
        self.breathing_animation.setEasingCurve(QtCore.QEasingCurve.Type.InOutSine)
        self.breathing_animation.setKeyValueAt(0.0, 16.0)
        self.breathing_animation.setKeyValueAt(0.5, 34.0)
        self.breathing_animation.setKeyValueAt(1.0, 16.0)
        self.breathing_animation.start()

    def stop_breathing_animation(self) -> None:
        """停止呼吸灯动画"""
        if self.breathing_animation:
            self.breathing_animation.stop()
            self.breathing_animation = None
        # 停止时移除发光效果，保证显示最稳
        if self._monitor_glow_effect is not None:
            self.monitor_btn.setGraphicsEffect(None)
            self._monitor_glow_effect = None

    def manual_debug_print(self) -> None:
        res = self.worker._last_raw_results
        self.log_output.appendPlainText(f"\n--- Debug Snapshot ({time.strftime('%H:%M:%S')}) ---")
        if not res:
            self.log_output.appendPlainText("未检测到内容。")
        else:
            for i, it in enumerate(res):
                self.log_output.appendPlainText(f"Block[{i}]: '{it[1]}' (conf: {it[2]:.4f})")

    def update_ui(self, text, conf, qimg, raw) -> None:  # noqa: ARG002
        # 更新状态文本
        display_text = text if text else "0"
        self.result_display.setText(display_text)
        self.conf_label.setText(f"平均置信度 {conf:.0%}" if conf > 0 else "平均置信度 --")

        # 判定报警状态
        is_alert = (conf >= ALARM_AVG_CONF_THRESHOLD) and (
            (any(c != "0" for c in text) if text else False)
        )

        # 用属性驱动 QSS，避免 setStyleSheet 覆盖全局主题
        self.result_display.setProperty("alert", "true" if is_alert else "false")
        self.result_display.style().unpolish(self.result_display)
        self.result_display.style().polish(self.result_display)

        self.status_title.setProperty("alert", "true" if is_alert else "false")
        self.status_title.setProperty("scanning", "false" if is_alert else "true")
        self.status_title.style().unpolish(self.status_title)
        self.status_title.style().polish(self.status_title)

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

        self.top_pill.style().unpolish(self.top_pill)
        self.top_pill.style().polish(self.top_pill)

        # 保存当前的预览图像
        self.current_preview_image = qimg

        # 必须开启预览按钮才更新图片
        if self.debug_btn.isChecked():
            self.preview_label.setPixmap(
                QtGui.QPixmap.fromImage(qimg).scaled(
                    self.preview_label.size(),
                    QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                    QtCore.Qt.TransformationMode.SmoothTransformation,
                )
            )

