from __future__ import annotations

import time
from collections import defaultdict
from typing import Optional

import cv2
import numpy as np
from PyQt6 import QtCore, QtGui

from .capture.screen_capture import ScreenCapture
from .constants import (
    ALARM_AVG_CONF_THRESHOLD,
    AUTO_SCALE_IF_SMALL,
    AUTO_SCALE_MIN_HEIGHT,
    AUTO_SCALE_MIN_WIDTH,
    AUTO_SCALE_VALUE,
    DEFAULT_OCR_SCALE,
    DEFAULT_TARGET_PERIOD_S,
    DEFAULT_USE_CLAHE,
    DEFAULT_USE_EQUALIZE_HIST,
)

try:
    import winsound  # type: ignore
except Exception:
    winsound = None


class Worker(QtCore.QThread):
    """后台 OCR 线程：截图 -> 预处理 -> OCR -> 结果合并 -> 发信号给 UI。"""

    # 信号：识别文字, 平均置信度, 预览图, 原始结果列表
    result_ready = QtCore.pyqtSignal(str, float, QtGui.QImage, list)

    def __init__(self) -> None:
        super().__init__()
        self.reader = None
        self.target_rect = None
        self.is_running = False
        self.show_debug = False
        self._last_raw_results: list = []
        self._is_processing = False  # 防止并发重入，保证同一时间只有一个 OCR 任务
        self._last_finish_ts = 0.0  # 上一次识别完成时间戳

        # 截屏与性能配置
        self._capture: Optional[ScreenCapture] = None  # 在工作线程内创建，避免跨线程的 thread-local 问题
        self.target_period_s = float(DEFAULT_TARGET_PERIOD_S)

        # 预处理参数：默认尽量省 CPU（需要更高准确率再调大）
        self.ocr_scale = float(DEFAULT_OCR_SCALE)  # 1.0 = 不放大；可改成 1.5 等
        self.auto_scale_if_small = bool(AUTO_SCALE_IF_SMALL)
        self.auto_scale_min_width = int(AUTO_SCALE_MIN_WIDTH)
        self.auto_scale_min_height = int(AUTO_SCALE_MIN_HEIGHT)
        self.auto_scale_value = float(AUTO_SCALE_VALUE)
        self.use_clahe = bool(DEFAULT_USE_CLAHE)
        # 默认关闭直方图均衡化：彩色输入通常不需要，有时反而会破坏颜色信息
        self.use_equalize_hist = bool(DEFAULT_USE_EQUALIZE_HIST)

    def stop(self) -> None:
        self.is_running = False

    def run(self) -> None:
        if not self.reader:
            return

        # mss 内部使用 thread-local 变量，必须在当前工作线程内初始化
        self._capture = ScreenCapture()
        try:
            try:
                self._capture.open()
            except Exception as e:
                # 允许回退到 ImageGrab
                print(f"初始化屏幕捕获失败，回退到 ImageGrab：{e}")
                try:
                    self._capture.close()
                except Exception:
                    pass

            while self.is_running:
                # 如果上一轮还在跑，或者距离上次完成不到 target_period_s，就等待
                if self._is_processing:
                    time.sleep(0.02)
                    continue

                if self._last_finish_ts > 0:
                    remain = float(self.target_period_s) - (time.time() - self._last_finish_ts)
                    if remain > 0:
                        time.sleep(min(remain, 0.05))
                        continue

                if not self.target_rect:
                    time.sleep(0.1)
                    continue

                self._is_processing = True
                loop_start = time.time()
                try:
                    # 1. 截图 (物理像素)
                    x1, y1, x2, y2 = self.target_rect
                    w = max(1, int(x2 - x1))
                    h = max(1, int(y2 - y1))

                    if self._capture is None:
                        raise RuntimeError("截屏器未初始化")
                    img_np = self._capture.grab_rgb(self.target_rect)  # RGB

                    # 2. 预处理：准备两种输入（彩色 + 灰度）
                    scale = float(self.ocr_scale)
                    if self.auto_scale_if_small and scale <= 1.0:
                        if w < self.auto_scale_min_width or h < self.auto_scale_min_height:
                            scale = float(self.auto_scale_value)

                    # 原彩色图（推荐用于游戏UI，对阴影/发光/抗锯齿效果更好）
                    color_for_ocr = img_np.copy()
                    if scale and abs(scale - 1.0) > 1e-3:
                        color_for_ocr = cv2.resize(
                            color_for_ocr, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC
                        )

                    # 灰度增强图（备用）
                    gray = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)
                    if scale and abs(scale - 1.0) > 1e-3:
                        gray_for_ocr = cv2.resize(
                            gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC
                        )
                    else:
                        gray_for_ocr = gray

                    if self.use_clahe:
                        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
                        gray_enhanced = clahe.apply(gray_for_ocr)
                    elif self.use_equalize_hist:
                        gray_enhanced = cv2.equalizeHist(gray_for_ocr)
                    else:
                        gray_enhanced = gray_for_ocr

                    # 3. 双路 OCR 识别：彩色优先，灰度补充
                    def ocr_extract_digits(img):
                        """执行 OCR 并提取纯数字结果"""
                        res, _ = self.reader(img)
                        extracted = []
                        if res:
                            for item in res:
                                if not isinstance(item, (list, tuple)) or len(item) < 3:
                                    continue
                                box, text, conf = item[0], item[1], float(item[2])
                                text = "".join(c for c in str(text) if c.isdigit())
                                if text:
                                    extracted.append((box, text, conf))
                        return extracted

                    # 彩色图 OCR（主要来源）
                    color_results = ocr_extract_digits(color_for_ocr)
                    # 灰度图 OCR（补充来源）
                    gray_results = ocr_extract_digits(gray_enhanced)

                    # 合并结果：优先彩色，再补灰度（避免重复框）
                    def box_key(box):
                        """生成框的唯一标识（基于中心位置，容差20像素）"""
                        cx = int((box[0][0] + box[2][0]) / 2) // 20
                        cy = int((box[0][1] + box[2][1]) / 2) // 20
                        return (cx, cy)

                    seen_boxes = set()
                    results = []

                    # 先添加彩色结果
                    for item in color_results:
                        key = box_key(item[0])
                        if key not in seen_boxes:
                            seen_boxes.add(key)
                            results.append(item)

                    # 再添加灰度结果中的新框
                    for item in gray_results:
                        key = box_key(item[0])
                        if key not in seen_boxes:
                            seen_boxes.add(key)
                            results.append(item)

                    self._last_raw_results = results

                    conf_sum, valid_results = 0.0, []
                    detected_nonzero = False

                    if results:
                        for res in results:
                            pos, text, conf = res[0], res[1], float(res[2])
                            box_w = abs(pos[1][0] - pos[0][0])
                            box_h = abs(pos[2][1] - pos[1][1])
                            ratio = box_w / (box_h if box_h > 0 else 1)

                            # 过滤干扰项
                            if conf < 0.35 and ratio < 0.15:
                                continue

                            if conf > 0.25:  # 降低一点点门槛，确保预览能看到
                                conf_sum += conf
                                valid_results.append(res)
                                if text != "0":
                                    detected_nonzero = True

                    # 智能合并：按Y中心线分组，再按X排序合并
                    groups = defaultdict(list)
                    for res in valid_results:
                        box = res[0]
                        # 计算Y中心线，每 20 像素高度分一组
                        center_y = int((box[0][1] + box[2][1]) / 2)
                        group_key = center_y // 20
                        groups[group_key].append(res)

                    merged_nums = []
                    for group_key in sorted(groups.keys()):
                        group = groups[group_key]
                        # 按左上角X坐标排序
                        group.sort(key=lambda r: r[0][0][0])
                        line_text = "".join(r[1] for r in group)
                        merged_nums.append(line_text)

                    display_text = "".join(merged_nums)
                    avg_conf = conf_sum / len(valid_results) if valid_results else 0.0
                    should_alarm = detected_nonzero and (avg_conf >= ALARM_AVG_CONF_THRESHOLD)

                    # 只有开启预览时才生成调试图，避免额外 CPU 开销
                    qimg = (
                        self.process_debug_img(img_np, valid_results, scale=scale)
                        if self.show_debug
                        else QtGui.QImage()
                    )
                    self.result_ready.emit(display_text, avg_conf, qimg, results)

                    if should_alarm and winsound is not None:
                        winsound.Beep(1000, 500)
                except Exception as e:
                    print(f"识别异常: {e}")
                finally:
                    self._is_processing = False
                    self._last_finish_ts = time.time()

                # 确保线程在极短处理耗时场景下也不会忙轮询
                if time.time() - loop_start < 0.01:
                    time.sleep(0.01)
        finally:
            try:
                if self._capture is not None:
                    self._capture.close()
            finally:
                self._capture = None

    def process_debug_img(self, img_np, results, scale: float = 1.0) -> QtGui.QImage:
        """增强版绘图逻辑：在图片上画框并标注文字"""
        img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)

        if results:
            for res in results:
                # 还原坐标：识别时可能放大了 scale 倍
                pos = np.array(res[0], np.float32)
                if scale and abs(scale - 1.0) > 1e-3:
                    pos = pos / float(scale)
                pos = pos.astype(np.int32)
                text = res[1]
                conf = float(res[2])

                # 颜色判定：0 绿色，非 0 红色
                color = (0, 0, 255) if text != "0" else (0, 255, 0)

                # 画矩形框
                cv2.polylines(img_bgr, [pos], True, color, 2)

                # 绘制文字标签背景 (黑色背景使白色文字更清晰)
                label = f"{text} ({conf:.2f})"
                font = cv2.FONT_HERSHEY_SIMPLEX
                font_scale = 0.5
                thickness = 1
                (w, h), baseline = cv2.getTextSize(label, font, font_scale, thickness)

                # 文字位置 (框的左上角上方)
                txt_x, txt_y = int(pos[0][0]), max(int(pos[0][1]) - 10, 20)
                cv2.rectangle(
                    img_bgr,
                    (txt_x, txt_y - h - baseline),
                    (txt_x + w, txt_y + baseline),
                    (0, 0, 0),
                    -1,
                )

                # 写入标注文字
                cv2.putText(
                    img_bgr,
                    label,
                    (txt_x, txt_y),
                    font,
                    font_scale,
                    (255, 255, 255),
                    thickness,
                )

        h, w, ch = img_bgr.shape
        return QtGui.QImage(
            img_bgr.data, w, h, w * ch, QtGui.QImage.Format.Format_BGR888
        ).copy()

