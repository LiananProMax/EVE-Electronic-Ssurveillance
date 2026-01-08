from __future__ import annotations

from typing import Optional, Tuple

import numpy as np

try:
    import mss  # type: ignore

    _HAS_MSS = True
except Exception:
    mss = None
    _HAS_MSS = False

try:
    from PIL import ImageGrab

    _HAS_IMAGEGRAB = True
except Exception:
    ImageGrab = None
    _HAS_IMAGEGRAB = False

Rect = Tuple[int, int, int, int]


class ScreenCapture:
    """屏幕抓取：优先 mss，失败回退到 Pillow.ImageGrab。"""

    def __init__(self) -> None:
        self._sct: Optional[object] = None

    def open(self) -> None:
        """在当前线程初始化 mss（mss 使用 thread-local，必须在使用线程内创建）。"""
        self.close()
        if not _HAS_MSS:
            self._sct = None
            return
        # mss 内部使用 thread-local 变量，必须在当前工作线程内初始化
        self._sct = mss.mss()

    def close(self) -> None:
        """释放底层句柄（若有）。"""
        if self._sct is not None and hasattr(self._sct, "close"):
            try:
                self._sct.close()
            except Exception:
                pass
        self._sct = None

    def grab_rgb(self, rect: Rect) -> np.ndarray:
        """截取指定区域，返回 RGB np.ndarray（H, W, 3）。"""
        x1, y1, x2, y2 = rect
        w = max(1, int(x2 - x1))
        h = max(1, int(y2 - y1))

        if self._sct is not None:
            monitor = {"left": int(x1), "top": int(y1), "width": w, "height": h}
            shot = np.array(self._sct.grab(monitor))  # BGRA
            # BGRA -> RGB（不依赖 cv2，减少耦合）
            rgb = shot[..., :3][:, :, ::-1].copy()
            return rgb

        if not _HAS_IMAGEGRAB:
            raise RuntimeError("截屏库不可用：请安装 mss，或确保 Pillow 的 ImageGrab 可用")

        img = ImageGrab.grab(bbox=rect, all_screens=True)
        arr = np.array(img)
        # Pillow 可能返回 RGB 或 RGBA；后续处理以 RGB 为主，尽量裁掉 alpha
        if arr.ndim == 3 and arr.shape[2] >= 3:
            return arr[:, :, :3]
        return arr

