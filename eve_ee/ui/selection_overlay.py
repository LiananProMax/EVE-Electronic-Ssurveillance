from __future__ import annotations

from PyQt6 import QtCore, QtGui, QtWidgets


class AreaSelectionOverlay(QtWidgets.QWidget):
    """全屏叠层：鼠标拖拽选择矩形区域，释放时发出 selection_made 信号。

    - 支持传入 allowed_rect（全局坐标/DIP）：限制只能在该矩形内拖拽框选
    - 为避免多屏/负坐标问题，selection_made 发出的是“全局坐标/DIP”的 QRect
    """

    selection_made = QtCore.pyqtSignal(QtCore.QRect)

    def __init__(self, *, allowed_rect: QtCore.QRect | None = None, hint_text: str = "") -> None:
        super().__init__()
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

        self._allowed_rect_global = allowed_rect
        self._hint_text = str(hint_text or "")

        self._begin = QtCore.QPoint()
        self._end = QtCore.QPoint()
        self._selecting = False

        # 覆盖整个虚拟桌面（多屏 + 负坐标）
        geom = QtCore.QRect()
        for s in QtGui.QGuiApplication.screens():
            geom = geom.united(s.geometry())
        if geom.isNull():
            geom = QtGui.QGuiApplication.primaryScreen().geometry()  # type: ignore[union-attr]
        self.setGeometry(geom)

    def showEvent(self, event) -> None:  # noqa: ARG002
        # 确保叠层真的在最上并拿到键盘（ESC 取消）
        try:
            self.raise_()
            self.activateWindow()
            self.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)
            self.grabKeyboard()
        except Exception:
            pass

    def closeEvent(self, event) -> None:  # noqa: ARG002
        try:
            self.releaseKeyboard()
        except Exception:
            pass

    def _allowed_rect_local(self) -> QtCore.QRect | None:
        if self._allowed_rect_global is None:
            return None
        # 将全局坐标转换到 widget 本地坐标（考虑多屏/负坐标的 origin）
        return self._allowed_rect_global.translated(-self.geometry().topLeft())

    def _to_global_rect(self, r_local: QtCore.QRect) -> QtCore.QRect:
        return r_local.translated(self.geometry().topLeft())

    def paintEvent(self, event) -> None:  # noqa: ARG002
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)

        # 1) 背景遮罩（注意：不要把允许区域画成“完全透明”，否则在部分 Windows 环境会出现鼠标事件穿透）
        allowed_local = self._allowed_rect_local()
        if allowed_local is not None and not allowed_local.isNull():
            # 先整体铺一层“轻遮罩”，保证允许区域也非 0-alpha，从而可接收鼠标事件
            painter.fillRect(self.rect(), QtGui.QColor(0, 0, 0, 18))

            # 再把允许区域之外加深（OddEvenFill 只填“外圈”）
            path = QtGui.QPainterPath()
            path.setFillRule(QtCore.Qt.FillRule.OddEvenFill)
            path.addRect(QtCore.QRectF(self.rect()))
            path.addRect(QtCore.QRectF(allowed_local))
            painter.fillPath(path, QtGui.QColor(0, 0, 0, 120))

            painter.setPen(QtGui.QPen(QtGui.QColor(20, 184, 166), 2))
            painter.drawRect(allowed_local.adjusted(1, 1, -1, -1))
        else:
            painter.fillRect(self.rect(), QtGui.QColor(0, 0, 0, 110))

        # 3) 提示文字
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

        # 4) 当前选择框
        if self._selecting:
            painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255), 2))
            painter.drawRect(QtCore.QRect(self._begin, self._end).normalized())

    def mousePressEvent(self, event) -> None:
        p = event.position().toPoint()
        allowed = self._allowed_rect_local()
        if allowed is not None and not allowed.contains(p):
            return
        self._begin = p
        self._end = self._begin
        self._selecting = True
        self.update()

    def mouseMoveEvent(self, event) -> None:
        if not self._selecting:
            return
        p = event.position().toPoint()
        allowed = self._allowed_rect_local()
        if allowed is not None:
            p.setX(min(max(p.x(), allowed.left()), allowed.right()))
            p.setY(min(max(p.y(), allowed.top()), allowed.bottom()))
        self._end = p
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if not self._selecting:
            self.close()
            return

        p = event.position().toPoint()
        allowed = self._allowed_rect_local()
        if allowed is not None:
            p.setX(min(max(p.x(), allowed.left()), allowed.right()))
            p.setY(min(max(p.y(), allowed.top()), allowed.bottom()))

        rect_local = QtCore.QRect(self._begin, p).normalized()
        if allowed is not None:
            rect_local = rect_local.intersected(allowed)

        rect = self._to_global_rect(rect_local)
        if rect.width() > 5 and rect.height() > 5:
            self.selection_made.emit(rect)
        self.close()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == int(QtCore.Qt.Key.Key_Escape):
            self.close()
            return
        super().keyPressEvent(event)
