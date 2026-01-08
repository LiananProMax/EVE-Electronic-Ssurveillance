from __future__ import annotations

from typing import Optional

from PyQt6 import QtCore, QtGui, QtWidgets

from ..win.window_api import WindowInfo, list_top_level_windows


class WindowPickerDialog(QtWidgets.QDialog):
    """让用户从当前系统顶层窗口中选择一个要监控的窗口。"""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("选择窗口")
        self.setModal(True)
        self.resize(720, 520)

        self._windows: list[WindowInfo] = []

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QtWidgets.QLabel("请选择要监控的窗口")
        title.setStyleSheet("font-size: 16px; font-weight: 700;")
        hint = QtWidgets.QLabel("提示：选择游戏/模拟器主窗口。选好后将进入“只在该窗口内拖拽框选区域”。")
        hint.setStyleSheet("color: #64748B;")
        hint.setWordWrap(True)

        layout.addWidget(title)
        layout.addWidget(hint)

        top_row = QtWidgets.QHBoxLayout()
        self.filter_edit = QtWidgets.QLineEdit()
        self.filter_edit.setPlaceholderText("输入关键字过滤窗口标题…")
        self.filter_edit.setClearButtonEnabled(True)
        top_row.addWidget(self.filter_edit, 1)

        self.refresh_btn = QtWidgets.QPushButton("刷新")
        self.refresh_btn.setFixedWidth(90)
        top_row.addWidget(self.refresh_btn, 0)
        layout.addLayout(top_row)

        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
        )
        self.list_widget.setUniformItemSizes(True)
        self.list_widget.setAlternatingRowColors(True)
        layout.addWidget(self.list_widget, 1)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch(1)
        self.ok_btn = QtWidgets.QPushButton("确定")
        self.ok_btn.setDefault(True)
        self.cancel_btn = QtWidgets.QPushButton("取消")
        btn_row.addWidget(self.cancel_btn)
        btn_row.addWidget(self.ok_btn)
        layout.addLayout(btn_row)

        self.refresh_btn.clicked.connect(self.reload)
        self.filter_edit.textChanged.connect(self._render)
        self.ok_btn.clicked.connect(self.accept)
        self.cancel_btn.clicked.connect(self.reject)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self.accept())

        # 初次加载
        QtCore.QTimer.singleShot(0, self.reload)

    def reload(self) -> None:
        try:
            self._windows = list_top_level_windows(include_minimized=True)
        except Exception as e:
            self._windows = []
            QtWidgets.QMessageBox.warning(self, "窗口枚举失败", str(e))
        self._render()

    def _render(self) -> None:
        key = (self.filter_edit.text() or "").strip().lower()
        self.list_widget.clear()
        for w in self._windows:
            if key and key not in w.title.lower():
                continue
            text = f"{w.title}    [0x{w.hwnd:08X}]"
            item = QtWidgets.QListWidgetItem(text)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, int(w.hwnd))
            self.list_widget.addItem(item)

        # 默认选中第一项
        if self.list_widget.count() > 0 and self.list_widget.currentRow() < 0:
            self.list_widget.setCurrentRow(0)

    def selected_window(self) -> Optional[WindowInfo]:
        item = self.list_widget.currentItem()
        if item is None:
            return None
        hwnd = int(item.data(QtCore.Qt.ItemDataRole.UserRole) or 0)
        if hwnd <= 0:
            return None
        title = item.text().split("    [0x", 1)[0].strip()
        return WindowInfo(hwnd=hwnd, title=title)


def pick_window(parent: QtWidgets.QWidget | None = None) -> Optional[WindowInfo]:
    dlg = WindowPickerDialog(parent)
    if dlg.exec() != int(QtWidgets.QDialog.DialogCode.Accepted):
        return None
    return dlg.selected_window()

