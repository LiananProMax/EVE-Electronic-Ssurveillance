from __future__ import annotations

from PyQt6 import QtCore, QtGui, QtWidgets


class AreaSelectionOverlay(QtWidgets.QWidget):
    """全屏透明叠层：鼠标拖拽选择矩形区域，释放时发出 selection_made 信号。"""

    selection_made = QtCore.pyqtSignal(QtCore.QRect)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
        )
        self.setWindowOpacity(0.3)
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)

        self._begin = QtCore.QPoint()
        self._end = QtCore.QPoint()
        self._selecting = False

    def paintEvent(self, event) -> None:  # noqa: ARG002
        if not self._selecting:
            return
        painter = QtGui.QPainter(self)
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255), 2))
        painter.drawRect(QtCore.QRect(self._begin, self._end))

    def mousePressEvent(self, event) -> None:
        self._begin = event.position().toPoint()
        self._end = self._begin
        self._selecting = True
        self.update()

    def mouseMoveEvent(self, event) -> None:
        if not self._selecting:
            return
        self._end = event.position().toPoint()
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if not self._selecting:
            self.close()
            return
        rect = QtCore.QRect(self._begin, event.position().toPoint()).normalized()
        if rect.width() > 5 and rect.height() > 5:
            self.selection_made.emit(rect)
        self.close()

