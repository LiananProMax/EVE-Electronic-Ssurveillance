"""区域选择覆盖层模块。

提供一个全屏半透明覆盖层，让用户通过鼠标拖拽选择矩形区域。
用于 OCR 监控应用中选择监控区域。

主要特性:
    - 支持限制选择范围（allowed_rect）
    - 支持指定目标屏幕（避免多屏坐标问题）
    - 半透明遮罩 + 高亮允许区域
    - ESC 键取消选择
"""
from __future__ import annotations

from PyQt6 import QtCore, QtGui, QtWidgets


class AreaSelectionOverlay(QtWidgets.QWidget):
    """全屏半透明叠层，用于鼠标拖拽选择矩形区域。

    工作流程:
        1. 显示覆盖层，遮挡整个屏幕/指定屏幕
        2. 用户在允许区域内按下鼠标开始选择
        3. 拖拽时显示选择框
        4. 释放鼠标时发出 selection_made 信号
        5. 覆盖层自动关闭

    Signals:
        selection_made(QRect): 选择完成时发出，参数为 Qt 全局逻辑坐标下的矩形。

    Attributes:
        _allowed_rect_global: 允许选择的区域（全局坐标），None 表示不限制
        _target_screen: 目标屏幕，None 表示覆盖所有屏幕
        _hint_text: 显示在覆盖层左上角的提示文字
    """

    selection_made = QtCore.pyqtSignal(QtCore.QRect)

    def __init__(
        self,
        *,
        allowed_rect: QtCore.QRect | None = None,
        hint_text: str = "",
        target_screen: QtGui.QScreen | None = None
    ) -> None:
        """初始化区域选择覆盖层。

        Args:
            allowed_rect: 允许选择的区域（Qt 全局逻辑坐标）。
                         如果指定，用户只能在此区域内拖拽框选，
                         区域外会显示更深的遮罩。
            hint_text: 左上角显示的提示文字（可多行）。
            target_screen: 覆盖层显示在哪个屏幕上。
                          如果指定，只覆盖该屏幕（推荐用于多屏幕环境）。
                          如果为 None，覆盖整个虚拟桌面。
        """
        super().__init__()

        # 窗口属性：无边框、始终置顶、工具窗口（不在任务栏显示）
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
        )
        # 启用透明背景
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # 十字光标
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)
        # 强焦点策略，确保能接收键盘事件
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        # 启用鼠标跟踪（即使不按下也能收到 mouseMoveEvent）
        self.setMouseTracking(True)

        # 保存配置
        self._allowed_rect_global = allowed_rect
        self._hint_text = str(hint_text or "")
        self._target_screen = target_screen

        # 选择状态
        self._begin = QtCore.QPoint()  # 选择起点（本地坐标）
        self._end = QtCore.QPoint()    # 选择终点（本地坐标）
        self._selecting = False         # 是否正在选择中

        # 计算覆盖层的几何区域
        geom = self._compute_overlay_geometry(target_screen)
        self.setGeometry(geom)

    def _compute_overlay_geometry(self, target_screen: QtGui.QScreen | None) -> QtCore.QRect:
        """计算覆盖层应该覆盖的屏幕区域。

        Args:
            target_screen: 目标屏幕，None 表示覆盖所有屏幕。

        Returns:
            覆盖层的几何矩形（Qt 全局坐标）。
        """
        if target_screen is not None:
            # 只覆盖指定屏幕
            return target_screen.geometry()

        # 覆盖整个虚拟桌面（所有屏幕的并集）
        geom = QtCore.QRect()
        for screen in QtGui.QGuiApplication.screens():
            geom = geom.united(screen.geometry())

        # 回退：如果没有屏幕信息，使用主屏幕
        if geom.isNull():
            primary = QtGui.QGuiApplication.primaryScreen()
            if primary is not None:
                geom = primary.geometry()
            else:
                # 极端情况：返回一个默认矩形
                geom = QtCore.QRect(0, 0, 800, 600)

        return geom

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # noqa: ARG002
        """处理窗口显示事件。

        确保覆盖层:
            1. 置于所有窗口最前
            2. 获得窗口激活状态
            3. 获得键盘焦点（用于 ESC 取消）
            4. 独占键盘输入
        """
        try:
            self.raise_()
            self.activateWindow()
            self.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)
            self.grabKeyboard()
        except Exception:
            # 某些环境下可能失败（如权限限制），但不影响基本功能
            pass

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: ARG002
        """处理窗口关闭事件。

        释放之前独占的键盘，避免影响其他程序。
        """
        try:
            self.releaseKeyboard()
        except Exception:
            pass

    def _allowed_rect_local(self) -> QtCore.QRect | None:
        """获取允许区域的本地坐标表示。

        将全局坐标的 allowed_rect 转换为相对于本覆盖层左上角的本地坐标。
        这在多屏幕环境下尤为重要，因为覆盖层可能从负坐标开始。

        Returns:
            本地坐标的允许区域矩形，如果未设置 allowed_rect 则返回 None。
        """
        if self._allowed_rect_global is None:
            return None
        return self._allowed_rect_global.translated(-self.geometry().topLeft())

    def _to_global_rect(self, r_local: QtCore.QRect) -> QtCore.QRect:
        """将本地坐标矩形转换为全局坐标。

        Args:
            r_local: 本地坐标矩形（相对于覆盖层左上角）。

        Returns:
            Qt 全局坐标矩形。
        """
        return r_local.translated(self.geometry().topLeft())

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: ARG002
        """绘制覆盖层。

        绘制内容:
            1. 背景遮罩（允许区域较亮，其他区域较暗）
            2. 允许区域边框（teal 色）
            3. 提示文字（左上角）
            4. 当前选择框（白色边框）

        Note:
            即使允许区域也需要轻微的遮罩（alpha > 0），
            否则在部分 Windows 环境下会出现鼠标事件穿透问题。
        """
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)

        allowed_local = self._allowed_rect_local()

        # --- 1. 背景遮罩 ---
        if allowed_local is not None and not allowed_local.isNull():
            # 先整体铺一层轻遮罩（alpha=18），保证允许区域也能接收鼠标事件
            painter.fillRect(self.rect(), QtGui.QColor(0, 0, 0, 18))

            # 使用 OddEvenFill 路径，在允许区域之外加深遮罩
            path = QtGui.QPainterPath()
            path.setFillRule(QtCore.Qt.FillRule.OddEvenFill)
            path.addRect(QtCore.QRectF(self.rect()))       # 外圈：整个覆盖层
            path.addRect(QtCore.QRectF(allowed_local))     # 内圈：允许区域
            painter.fillPath(path, QtGui.QColor(0, 0, 0, 120))

            # 绘制允许区域边框（teal 色）
            painter.setPen(QtGui.QPen(QtGui.QColor(20, 184, 166), 2))
            painter.drawRect(allowed_local.adjusted(1, 1, -1, -1))
        else:
            # 无限制模式：整体半透明遮罩
            painter.fillRect(self.rect(), QtGui.QColor(0, 0, 0, 110))

        # --- 2. 提示文字 ---
        if self._hint_text:
            painter.setPen(QtGui.QColor(255, 255, 255, 230))
            font = painter.font()
            font.setPointSize(max(10, font.pointSize()))
            painter.setFont(font)
            painter.drawText(
                self.rect().adjusted(16, 12, -16, -12),
                QtCore.Qt.AlignmentFlag.AlignTop | QtCore.Qt.AlignmentFlag.AlignLeft,
                self._hint_text,
            )

        # --- 3. 当前选择框 ---
        if self._selecting:
            painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255), 2))
            selection_rect = QtCore.QRect(self._begin, self._end).normalized()
            painter.drawRect(selection_rect)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        """处理鼠标按下事件。

        如果在允许区域内按下，开始选择操作。
        如果在允许区域外按下，忽略（不开始选择）。
        """
        pos = event.position().toPoint()
        allowed = self._allowed_rect_local()

        # 检查是否在允许区域内
        if allowed is not None and not allowed.contains(pos):
            return  # 点击在允许区域外，忽略

        # 开始选择
        self._begin = pos
        self._end = pos
        self._selecting = True
        self.update()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        """处理鼠标移动事件。

        如果正在选择，更新选择框的终点位置。
        终点会被 clamp 到允许区域内。
        """
        if not self._selecting:
            return

        pos = event.position().toPoint()
        allowed = self._allowed_rect_local()

        # 将终点 clamp 到允许区域内
        if allowed is not None:
            pos.setX(max(allowed.left(), min(pos.x(), allowed.right())))
            pos.setY(max(allowed.top(), min(pos.y(), allowed.bottom())))

        self._end = pos
        self.update()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        """处理鼠标释放事件。

        完成选择：
            1. 计算最终选择矩形
            2. 将矩形 clamp 到允许区域内
            3. 如果矩形足够大（>5x5像素），发出 selection_made 信号
            4. 关闭覆盖层
        """
        if not self._selecting:
            # 没有开始选择就释放（可能是在允许区域外点击），直接关闭
            self.close()
            return

        pos = event.position().toPoint()
        allowed = self._allowed_rect_local()

        # 将终点 clamp 到允许区域内
        if allowed is not None:
            pos.setX(max(allowed.left(), min(pos.x(), allowed.right())))
            pos.setY(max(allowed.top(), min(pos.y(), allowed.bottom())))

        # 计算选择矩形（normalized 确保 left < right, top < bottom）
        rect_local = QtCore.QRect(self._begin, pos).normalized()

        # 与允许区域取交集
        if allowed is not None:
            rect_local = rect_local.intersected(allowed)

        # 转换为全局坐标
        rect_global = self._to_global_rect(rect_local)

        # 只有足够大的选择才有效（过滤误点击）
        min_size = 5
        if rect_global.width() > min_size and rect_global.height() > min_size:
            self.selection_made.emit(rect_global)

        self.close()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        """处理键盘事件。

        ESC 键：取消选择并关闭覆盖层。
        """
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)
