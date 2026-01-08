from __future__ import annotations

import sys
from typing import Any, Optional

from PyQt6 import QtCore, QtWidgets

from .ui.main_window import MainWindow


def run(*, ort: Optional[Any] = None) -> None:
    """启动 Qt 应用。"""

    # 正确的 DPI 初始化顺序
    QtWidgets.QApplication.setHighDpiScaleFactorRoundingPolicy(
        QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow(ort=ort)
    win.show()
    raise SystemExit(app.exec())

