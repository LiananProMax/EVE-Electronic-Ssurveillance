import os

# 尽早限制推理线程数：必须放在 onnxruntime / rapidocr 导入之前才更有效
# 说明：用户仍可通过环境变量覆盖这些默认值
os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("MKL_NUM_THREADS", "2")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "2")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "2")

try:
    import onnxruntime as ort  # RapidOCR 底层依赖

    # 关闭冗余日志，减少开销
    ort.set_default_logger_severity(3)
except Exception:
    ort = None

import sys
import cv2
import numpy as np
import time
import winsound
from collections import defaultdict
from PyQt6 import QtWidgets, QtCore, QtGui

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

from rapidocr_onnxruntime import RapidOCR

# 强制禁用 opencv 的多线程，防止冲突
cv2.setNumThreads(0)

# 报警平均置信度阈值：要求达到 6.5/10（约 65%）才算有效
ALARM_AVG_CONF_THRESHOLD = 0.65

class Worker(QtCore.QThread):
    # 信号：识别文字, 平均置信度, 预览图, 原始结果列表
    result_ready = QtCore.pyqtSignal(str, float, QtGui.QImage, list)
    
    def __init__(self):
        super().__init__()
        self.reader = None 
        self.target_rect = None 
        self.is_running = False
        self.show_debug = False
        self._last_raw_results = []
        self._is_processing = False  # 防止并发重入，保证同一时间只有一个 OCR 任务
        self._last_finish_ts = 0.0   # 上一次识别完成时间戳

        # 截屏与性能配置
        self._sct = None  # 在工作线程内创建，避免跨线程的 mss thread-local 问题
        self.target_period_s = 2  # 目标约每 2 秒 1 帧（动态 sleep，会自动补偿处理耗时）

        # 预处理参数：默认尽量省 CPU（需要更高准确率再调大）
        self.ocr_scale = 1.0  # 1.0 = 不放大；可改成 1.5 等
        self.auto_scale_if_small = True
        self.auto_scale_min_width = 300
        self.auto_scale_min_height = 120
        self.auto_scale_value = 1.5
        self.use_clahe = False
        # 默认关闭直方图均衡化：彩色输入通常不需要，有时反而会破坏颜色信息
        self.use_equalize_hist = False

    def run(self):
        if not self.reader: return
        # mss 内部使用 thread-local 变量，必须在当前工作线程内初始化
        if _HAS_MSS:
            try:
                # 避免复用旧线程创建的 mss 实例，重建以绑定当前线程
                if self._sct and hasattr(self._sct, "close"):
                    try: self._sct.close()
                    except Exception: pass
                self._sct = mss.mss()
            except Exception as e:
                print(f"初始化屏幕捕获失败，回退到 ImageGrab：{e}")
                self._sct = None
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

                if self._sct is not None:
                    monitor = {"left": int(x1), "top": int(y1), "width": w, "height": h}
                    shot = np.array(self._sct.grab(monitor))  # BGRA
                    img_np = cv2.cvtColor(shot, cv2.COLOR_BGRA2RGB)
                else:
                    if not _HAS_IMAGEGRAB:
                        raise RuntimeError("截屏库不可用：请安装 mss，或确保 Pillow 的 ImageGrab 可用")
                    img = ImageGrab.grab(bbox=self.target_rect, all_screens=True)
                    img_np = np.array(img)
                
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
                
                # 再添加灰度结果中的新框（置信度更高的情况）
                for item in gray_results:
                    key = box_key(item[0])
                    if key not in seen_boxes:
                        seen_boxes.add(key)
                        results.append(item)

                self._last_raw_results = results

                conf_sum, valid_results = 0, []
                detected_nonzero = False
                
                if results:
                    for res in results:
                        pos, text, conf = res[0], res[1], res[2]
                        box_w = abs(pos[1][0] - pos[0][0])
                        box_h = abs(pos[2][1] - pos[1][1])
                        ratio = box_w / (box_h if box_h > 0 else 1)
                        
                        # 过滤干扰项
                        if conf < 0.35 and ratio < 0.15: continue
                        
                        if conf > 0.25:  # 降低一点点门槛，确保预览能看到
                            conf_sum += conf
                            valid_results.append(res)
                            if text != "0": detected_nonzero = True
                
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
                qimg = self.process_debug_img(img_np, valid_results, scale=scale) if self.show_debug else QtGui.QImage()
                self.result_ready.emit(display_text, avg_conf, qimg, results)
                
                if should_alarm:
                    winsound.Beep(1000, 500)
            except Exception as e:
                print(f"识别异常: {e}")
            finally:
                self._is_processing = False
                self._last_finish_ts = time.time()

            # 确保线程在极短处理耗时场景下也不会忙轮询
            if time.time() - loop_start < 0.01:
                time.sleep(0.01)

    def process_debug_img(self, img_np, results, scale: float = 1.0):
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
                conf = res[2]
                
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
                txt_x, txt_y = pos[0][0], max(pos[0][1] - 10, 20)
                cv2.rectangle(img_bgr, (txt_x, txt_y - h - baseline), (txt_x + w, txt_y + baseline), (0, 0, 0), -1)
                
                # 写入标注文字
                cv2.putText(img_bgr, label, (txt_x, txt_y), font, font_scale, (255, 255, 255), thickness)
        
        h, w, ch = img_bgr.shape
        return QtGui.QImage(img_bgr.data, w, h, w * ch, QtGui.QImage.Format.Format_BGR888).copy()

class MainWindow(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("MainWindow")
        self.setWindowTitle("敌对中立监控程序")
        # 设置初始大小和最小大小，但允许用户调整窗口
        self.resize(560, 800)
        self.setMinimumSize(400, 600)
        self.screen_ratio = self.devicePixelRatio()
        self.readers = {"CPU": None}
        self.worker = Worker()
        self.worker.result_ready.connect(self.update_ui)
        
        # 呼吸灯动画
        self.breathing_animation = None
        self.breathing_opacity = 1.0
        self._monitor_glow_effect = None
        
        # 保存当前预览图像，用于窗口缩放时重新渲染
        self.current_preview_image = None
        
        self.init_ui()
        self.apply_styles()

    def init_ui(self):
        def add_shadow(w: QtWidgets.QWidget, blur=28, y=10, alpha=26):
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
        header_layout.addWidget(self.top_pill, 0, QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)

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
        preview_head.addWidget(self.debug_btn, 0, QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)

        preview_card_layout.addLayout(preview_head)

        self.preview_label = QtWidgets.QLabel("实时流已限制")
        self.preview_label.setMinimumSize(360, 220)
        self.preview_label.setObjectName("PreviewWindow")
        self.preview_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setScaledContents(False)
        # 设置大小策略：水平和垂直都可扩展
        self.preview_label.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding, 
            QtWidgets.QSizePolicy.Policy.Expanding
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
        action_sub = QtWidgets.QLabel("选择区域后开始监控")
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
        btn_grid.addWidget(self.monitor_btn, 1, 0, 1, 2) # 跨两列
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
            QtWidgets.QSizePolicy.Policy.Expanding
        )
        log_layout.addWidget(self.log_output)
        add_shadow(self.log_card)
        self.main_layout.addWidget(self.log_card)

        # 绑定事件
        self.select_btn.clicked.connect(self.start_selection)
        self.monitor_btn.clicked.connect(self.toggle_monitoring)
        self.print_btn.clicked.connect(self.manual_debug_print)
        self.debug_btn.toggled.connect(self.on_debug_toggled)

    def on_debug_toggled(self, checked: bool):
        self.debug_btn.setText("关闭" if checked else "开启")
        self.worker.show_debug = bool(checked)

    def resizeEvent(self, event):
        """窗口大小改变时的处理"""
        super().resizeEvent(event)
        # 如果有预览图片，重新缩放以适应新的窗口大小
        if hasattr(self, 'current_preview_image') and self.current_preview_image is not None:
            if self.debug_btn.isChecked():
                self.preview_label.setPixmap(QtGui.QPixmap.fromImage(self.current_preview_image).scaled(
                    self.preview_label.size(), 
                    QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                    QtCore.Qt.TransformationMode.SmoothTransformation))
    
    def apply_styles(self):
        """应用现代化简约清新主题样式（浅色/卡片化/低饱和强调色）"""
        self.setStyleSheet("""
            QWidget#MainWindow {
                background-color: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 #F7FAFF,
                    stop:1 #F4F6FB
                );
                color: #111827;
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
            }

            QLabel#AppTitle {
                font-size: 18px;
                font-weight: 750;
                color: #0F172A;
            }

            QLabel#AppSubtitle {
                font-size: 12px;
                color: #64748B;
            }

            QLabel#StatusPill {
                padding: 5px 10px;
                border-radius: 999px;
                border: 1px solid #E5E7EB;
                background: #FFFFFF;
                color: #334155;
                font-size: 12px;
                font-weight: 600;
            }

            QLabel#StatusPill[tone="neutral"] {
                background: #FFFFFF;
                border-color: #E5E7EB;
                color: #334155;
            }

            QLabel#StatusPill[tone="info"] {
                background: #E0F2FE;
                border-color: #7DD3FC;
                color: #0369A1;
            }

            QLabel#StatusPill[tone="danger"] {
                background: #FEE2E2;
                border-color: #FCA5A5;
                color: #B91C1C;
            }

            QFrame#Card {
                background-color: #FFFFFF;
                border: 1px solid #E6EAF2;
                border-radius: 16px;
            }
            
            #DisplayCard {
                background-color: #FFFFFF;
                border: 1px solid #E6EAF2;
                border-radius: 18px;
            }

            QLabel#CardTitle {
                font-size: 13px;
                font-weight: 700;
                color: #0F172A;
            }

            QLabel#CardHint {
                font-size: 12px;
                color: #64748B;
            }

            QLabel#MetaText {
                font-size: 12px;
                color: #64748B;
            }
            
            #BigNumber {
                font-size: 68px;
                font-weight: 900;
                color: #14B8A6;
                background: transparent;
                margin: 6px 0;
            }

            QLabel#BigNumber[alert="true"] {
                color: #EF4444;
            }

            QLabel#StatusTitle {
                font-size: 12px;
                color: #6B7280;
                letter-spacing: 2px;
            }

            QLabel#StatusTitle[alert="true"] {
                color: #EF4444;
            }

            QLabel#StatusTitle[scanning="true"] {
                color: #0EA5E9;
            }
            
            #PreviewWindow {
                background-color: #0B1220;
                border: 1px solid #111827;
                border-radius: 14px;
                color: #94A3B8;
                font-size: 13px;
            }
            
            QPushButton {
                background-color: #F3F4F6;
                border: 1px solid #E5E7EB;
                border-radius: 10px;
                padding: 10px 12px;
                font-weight: 600;
                font-size: 13px;
            }
            
            QPushButton:hover {
                background-color: #EEF2F7;
            }
            
            QPushButton:pressed {
                background-color: #E5E7EB;
            }
            
            QPushButton:checked {
                background-color: #E0F2FE;
                border-color: #7DD3FC;
                color: #0369A1;
            }

            QPushButton#GhostToggle {
                background-color: #FFFFFF;
                border: 1px solid #E5E7EB;
                border-radius: 10px;
                padding: 8px 12px;
                font-weight: 700;
            }

            QPushButton#GhostToggle:checked {
                background-color: #ECFDF5;
                border-color: #6EE7B7;
                color: #065F46;
            }
            
            #PrimaryBtn {
                background-color: #14B8A6; /* fallback */
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #14B8A6,
                    stop:1 #10B981
                );
                border-color: #0D9488;
                font-size: 15px;
                font-weight: 700;
                margin-top: 5px;
                color: #FFFFFF;
            }
            
            #PrimaryBtn:hover {
                background: #10B981;
                border-color: #059669;
            }
            
            #PrimaryBtn:pressed {
                background: #0F766E;
                border-color: #115E59;
            }

            QPushButton#PrimaryBtn[state="running"] {
                background-color: #EF4444; /* fallback */
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #EF4444,
                    stop:1 #F97316
                );
                border-color: #DC2626;
                color: #FFFFFF;
            }

            QPushButton#PrimaryBtn[state="running"]:hover {
                background: #F87171;
                border-color: #EF4444;
            }
            
            QComboBox {
                background-color: #FFFFFF;
                border: 1px solid #E5E7EB;
                border-radius: 10px;
                padding: 8px 10px;
                font-size: 13px;
            }
            
            QComboBox:hover {
                border: 1px solid #D1D5DB;
            }
            
            QComboBox::drop-down {
                border: none;
                width: 30px;
            }
            
            QComboBox::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 6px solid #6B7280;
                margin-right: 8px;
            }
            
            QComboBox QAbstractItemView {
                background-color: #FFFFFF;
                border: 1px solid #E5E7EB;
                selection-background-color: #E0F2FE;
                outline: none;
            }
            
            QPlainTextEdit {
                background-color: #FFFFFF;
                border: 1px solid #E5E7EB;
                border-radius: 12px;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 11px;
                color: #334155;
                padding: 10px;
            }

            QPlainTextEdit:focus {
                border: 1px solid #7DD3FC;
            }

            QScrollBar:vertical {
                background: transparent;
                width: 10px;
                margin: 8px 4px 8px 0px;
            }

            QScrollBar::handle:vertical {
                background: #CBD5E1;
                min-height: 28px;
                border-radius: 5px;
            }

            QScrollBar::handle:vertical:hover {
                background: #94A3B8;
            }

            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

    def get_reader(self):
        m_key = "CPU"
        if self.readers[m_key] is None:
            self.log_output.appendPlainText("⏳ 正在加载 RapidOCR 引擎...")
            QtWidgets.QApplication.setOverrideCursor(QtGui.QCursor(QtCore.Qt.CursorShape.WaitCursor))
            try:
                # 更精细的 ONNX Runtime 线程控制
                intra_threads = 2  # 单算子内部线程
                inter_threads = 2  # 算子间线程
                
                # 创建优化后的 SessionOptions
                sess_opts = None
                if ort is not None:
                    sess_opts = ort.SessionOptions()
                    sess_opts.intra_op_num_threads = intra_threads
                    sess_opts.inter_op_num_threads = inter_threads
                    sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                
                # RapidOCR 初始化：强制 CPU 并应用线程控制
                # 注意：RapidOCR 1.x 版本通过构造函数参数控制
                self.readers[m_key] = RapidOCR(
                    det_use_cuda=False,
                    cls_use_cuda=False,
                    rec_use_cuda=False,
                    # 线程控制参数（RapidOCR >= 1.3 支持）
                    intra_op_num_threads=intra_threads,
                    inter_op_num_threads=inter_threads,
                )
                self.log_output.appendPlainText(f"✅ RapidOCR 引擎已就绪（CPU，线程限制: {intra_threads}）。")
            except TypeError:
                # 如果 RapidOCR 版本不支持线程参数，回退到基础初始化
                try:
                    self.readers[m_key] = RapidOCR(
                        det_use_cuda=False,
                        cls_use_cuda=False,
                        rec_use_cuda=False,
                    )
                    self.log_output.appendPlainText("✅ RapidOCR 引擎已就绪（CPU，环境变量线程控制）。")
                except Exception as e2:
                    self.log_output.appendPlainText(f"❌ 加载失败：{e2}")
            except Exception as e:
                self.log_output.appendPlainText(f"❌ 加载失败：{e}")
            finally:
                QtWidgets.QApplication.restoreOverrideCursor()
        return self.readers[m_key]

    def start_selection(self):
        from PyQt6.QtWidgets import QWidget
        class Cap(QWidget):
            sel = QtCore.pyqtSignal(QtCore.QRect)
            def __init__(self):
                super().__init__()
                self.setWindowFlags(QtCore.Qt.WindowType.FramelessWindowHint | QtCore.Qt.WindowType.WindowStaysOnTopHint)
                self.setWindowState(QtCore.Qt.WindowState.WindowMaximized); self.setWindowOpacity(0.3)
                self.b = self.e = QtCore.QPoint(); self.s = False
            def paintEvent(self, ev):
                if self.s: QtGui.QPainter(self).drawRect(QtCore.QRect(self.b, self.e))
            def mousePressEvent(self, ev): self.b = ev.pos(); self.s = True
            def mouseMoveEvent(self, ev): self.e = ev.pos(); self.update()
            def mouseReleaseEvent(self, ev):
                r = QtCore.QRect(self.b, ev.pos()).normalized()
                if r.width()>5: self.sel.emit(r)
                self.close()
        self.cw = Cap(); self.cw.sel.connect(self.on_area); self.cw.show()

    def on_area(self, r):
        ratio = self.screen_ratio
        x1, y1 = max(0, int(r.x()*ratio)-8), max(0, int(r.y()*ratio)-8)
        x2, y2 = int(r.right()*ratio)+8, int(r.bottom()*ratio)+8
        self.worker.target_rect = (x1, y1, x2, y2)
        self.log_output.appendPlainText(f"🎯 已选择区域：{x2-x1}x{y2-y1} 像素")

    def toggle_monitoring(self):
        if not self.worker.is_running:
            reader = self.get_reader()
            if not reader: return
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
            self.worker.is_running = False
            
            # 清理 mss 资源，释放系统句柄
            if self.worker._sct is not None:
                try:
                    self.worker._sct.close()
                except Exception:
                    pass
                self.worker._sct = None
            
            self.monitor_btn.setText("开始监控")
            self.monitor_btn.setProperty("state", "idle")
            self.monitor_btn.style().unpolish(self.monitor_btn)
            self.monitor_btn.style().polish(self.monitor_btn)
            self.top_pill.setText("就绪")
            self.top_pill.setProperty("tone", "neutral")
            self.top_pill.style().unpolish(self.top_pill)
            self.top_pill.style().polish(self.top_pill)
            self.stop_breathing_animation()

    def start_breathing_animation(self):
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

        self.breathing_animation = QtCore.QPropertyAnimation(self._monitor_glow_effect, b"blurRadius")
        self.breathing_animation.setDuration(1400)
        self.breathing_animation.setLoopCount(-1)  # 无限循环
        self.breathing_animation.setEasingCurve(QtCore.QEasingCurve.Type.InOutSine)
        self.breathing_animation.setKeyValueAt(0.0, 16.0)
        self.breathing_animation.setKeyValueAt(0.5, 34.0)
        self.breathing_animation.setKeyValueAt(1.0, 16.0)
        self.breathing_animation.start()
    
    def stop_breathing_animation(self):
        """停止呼吸灯动画"""
        if self.breathing_animation:
            self.breathing_animation.stop()
            self.breathing_animation = None
        # 停止时移除发光效果，保证显示最稳
        if self._monitor_glow_effect is not None:
            self.monitor_btn.setGraphicsEffect(None)
            self._monitor_glow_effect = None

    def manual_debug_print(self):
        res = self.worker._last_raw_results
        self.log_output.appendPlainText(f"\n--- Debug Snapshot ({time.strftime('%H:%M:%S')}) ---")
        if not res: 
            self.log_output.appendPlainText("未检测到内容。")
        else:
            for i, it in enumerate(res):
                self.log_output.appendPlainText(f"Block[{i}]: '{it[1]}' (conf: {it[2]:.4f})")

    def update_ui(self, text, conf, qimg, raw):
        # 更新状态文本
        display_text = text if text else "0"
        self.result_display.setText(display_text)
        self.conf_label.setText(f"平均置信度 {conf:.0%}" if conf > 0 else "平均置信度 --")
        
        # 判定报警状态
        is_alert = (conf >= ALARM_AVG_CONF_THRESHOLD) and (any(c != '0' for c in text) if text else False)

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
            self.preview_label.setPixmap(QtGui.QPixmap.fromImage(qimg).scaled(
                self.preview_label.size(), 
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation))

if __name__ == "__main__":
    # 正确的 DPI 初始化顺序
    QtWidgets.QApplication.setHighDpiScaleFactorRoundingPolicy(
        QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())