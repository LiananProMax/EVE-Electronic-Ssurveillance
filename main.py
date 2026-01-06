import sys
import cv2
import numpy as np
import time
import winsound
from PyQt6 import QtWidgets, QtCore, QtGui
from PIL import ImageGrab
import easyocr

# 解决 Windows DPI 缩放导致的坐标偏移
if sys.platform == 'win32':
    from ctypes import windll
    windll.user32.SetProcessDPIAware()

class Worker(QtCore.QThread):
    # 信号：识别文字, 平均置信度, 预览图, 原始结果列表
    result_ready = QtCore.pyqtSignal(str, float, QtGui.QImage, list)
    
    def __init__(self, reader):
        super().__init__()
        self.reader = reader
        self.target_rect = None 
        self.is_running = False
        self.show_debug = False
        self._last_raw_results = [] # 缓存最近一次识别结果供手动调试

    def run(self):
        while self.is_running:
            if self.target_rect:
                try:
                    # 1. 截图
                    img = ImageGrab.grab(bbox=self.target_rect, all_screens=True)
                    img_np = np.array(img)
                    
                    # 2. 【核心优化】图像增强预处理 (解决灰底灰字识别不到的问题)
                    gray = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)
                    
                    # A. 放大图片：放大 2 倍使笔画更清晰
                    gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
                    
                    # B. 增加对比度：自适应直方图均衡化
                    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
                    enhanced = clahe.apply(gray)
                    
                    # C. 二值化处理
                    _, thresh = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                    
                    # 3. 执行 OCR 识别
                    results = self.reader.readtext(thresh, allowlist='0123456789')
                    self._last_raw_results = results 

                    all_nums = []
                    conf_sum = 0
                    should_alarm = False
                    valid_results = []
                    
                    if results:
                        for res in results:
                            pos = res[0]  # 坐标: [[x1,y1], [x2,y1], [x2,y2], [x1,y2]]
                            text = res[1]
                            conf = res[2]
                            
                            # --- 【新增：形状过滤逻辑】 ---
                            # 计算识别块的宽度和高度
                            w = abs(pos[1][0] - pos[0][0])
                            h = abs(pos[2][1] - pos[1][1])
                            
                            # 1. 过滤极其瘦长的物体（可能是感叹号的竖线）
                            aspect_ratio = w / h if h > 0 else 0
                            
                            # 2. 过滤面积过小的杂质
                            area = w * h
                            
                            # 如果置信度太低（<0.5）且形状太瘦（宽度不足高度的15%），判定为干扰
                            if conf < 0.5 and aspect_ratio < 0.15:
                                continue
                                
                            # 如果信心达到 0.35 以上且不是过于畸形的形状，才计入
                            if conf > 0.35:
                                all_nums.append(text)
                                conf_sum += conf
                                valid_results.append(res)
                                # 报警逻辑：只要有数字不是 "0"
                                if text != "0":
                                    should_alarm = True
                    
                    display_text = "".join(all_nums) if all_nums else ""
                    avg_conf = conf_sum / len(valid_results) if valid_results else 0.0
                    
                    # 生成预览图 (在原图上绘制识别框)
                    debug_qimg = self.process_enhanced_debug(img_np, valid_results, should_alarm)
                    self.result_ready.emit(display_text, avg_conf, debug_qimg, results)

                    if should_alarm:
                        winsound.Beep(1000, 500)

                except Exception as e:
                    print(f"工作线程异常: {e}")
            
            time.sleep(0.5)  # 识别频率：每秒 2 次

    def process_enhanced_debug(self, img_np, results, triggered):
        """在图片上标注识别结果"""
        img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
        box_color = (0, 0, 255) if triggered else (0, 255, 0)
        
        if self.show_debug and results:
            for res in results:
                # 注意：因为识别时图放大了2倍，坐标需要除以2回传
                pos = np.array(res[0], np.int32) // 2 
                text = res[1]
                cv2.polylines(img_bgr, [pos], True, box_color, 2)
                cv2.putText(img_bgr, text, (pos[0][0], pos[0][1] - 5), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        
        h, w, ch = img_bgr.shape
        return QtGui.QImage(img_bgr.data, w, h, w * ch, QtGui.QImage.Format.Format_BGR888).copy()

class CaptureWindow(QtWidgets.QWidget):
    """透明截图层"""
    area_selected = QtCore.pyqtSignal(QtCore.QRect)

    def __init__(self):
        super().__init__()
        self.setWindowFlags(QtCore.Qt.WindowType.FramelessWindowHint | QtCore.Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowState(QtCore.Qt.WindowState.WindowMaximized)
        self.setWindowOpacity(0.3)
        self.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.CrossCursor))
        self.begin = self.end = QtCore.QPoint()
        self.is_selecting = False

    def paintEvent(self, event):
        if self.is_selecting:
            p = QtGui.QPainter(self)
            p.setPen(QtGui.QPen(QtGui.QColor(0, 255, 0), 2))
            p.setBrush(QtGui.QColor(0, 255, 0, 50))
            p.drawRect(QtCore.QRect(self.begin, self.end))

    def mousePressEvent(self, e):
        self.begin = e.pos()
        self.is_selecting = True

    def mouseMoveEvent(self, e):
        self.end = e.pos()
        self.update()

    def mouseReleaseEvent(self, e):
        rect = QtCore.QRect(self.begin, e.pos()).normalized()
        if rect.width() > 10:
            self.area_selected.emit(rect)
        self.close()

class MainWindow(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("数字监控报警")
        self.setFixedSize(500, 680)
        
        # 获取系统缩放
        self.screen_ratio = QtWidgets.QApplication.primaryScreen().devicePixelRatio()
        
        # 初始化 OCR (根据环境可选 gpu=True/False)
        print("正在加载 OCR 模型...")
        self.reader = easyocr.Reader(['en'], gpu=True) 
        
        self.worker = Worker(self.reader)
        self.worker.result_ready.connect(self.update_ui)
        self.init_ui()

    def init_ui(self):
        layout = QtWidgets.QVBoxLayout()

        # 结果大屏
        self.result_display = QtWidgets.QLabel("等待开始")
        self.result_display.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.result_display.setStyleSheet("""
            font-size: 50px; font-weight: bold; color: #00FF00; 
            background: black; border-radius: 10px; min-height: 120px;
        """)
        layout.addWidget(self.result_display)

        # 预览视窗
        self.preview_label = QtWidgets.QLabel("预览区域 (开启预览后可见)")
        self.preview_label.setFixedSize(480, 160)
        self.preview_label.setStyleSheet("border: 2px dashed #666; background: #333; color: #eee;")
        self.preview_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.preview_label)

        # 日志输出
        self.log_output = QtWidgets.QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setStyleSheet("background: #f8f8f8; font-family: Consolas;")
        layout.addWidget(self.log_output)

        # 按钮网格
        grid = QtWidgets.QGridLayout()
        self.select_btn = QtWidgets.QPushButton("🔍 1. 选取区域")
        self.monitor_btn = QtWidgets.QPushButton("▶ 2. 开始监控")
        self.debug_btn = QtWidgets.QPushButton("🛠 开启预览")
        self.debug_btn.setCheckable(True)
        self.print_btn = QtWidgets.QPushButton("📸 调试快照")
        self.print_btn.setStyleSheet("background-color: #3498db; color: white;")

        self.select_btn.setFixedHeight(40)
        self.monitor_btn.setFixedHeight(40)

        grid.addWidget(self.select_btn, 0, 0)
        grid.addWidget(self.monitor_btn, 0, 1)
        grid.addWidget(self.debug_btn, 1, 0)
        grid.addWidget(self.print_btn, 1, 1)
        layout.addLayout(grid)

        # 事件绑定
        self.select_btn.clicked.connect(self.start_selection)
        self.monitor_btn.clicked.connect(self.toggle_monitoring)
        self.debug_btn.clicked.connect(self.toggle_debug)
        self.print_btn.clicked.connect(self.manual_debug_print)

        self.setLayout(layout)

    def start_selection(self):
        self.cap_win = CaptureWindow()
        self.cap_win.area_selected.connect(self.on_area_done)
        self.cap_win.show()

    def on_area_done(self, rect):
        r = self.screen_ratio
        # 给选区增加 8 像素的缓冲带，防止压线
        x1 = max(0, int(rect.x() * r) - 8)
        y1 = max(0, int(rect.y() * r) - 8)
        x2 = int(rect.right() * r) + 8
        y2 = int(rect.bottom() * r) + 8
        
        self.worker.target_rect = (x1, y1, x2, y2)
        self.log_output.appendPlainText(f"区域已校准: {x2-x1}x{y2-y1} (DPI={r})")

    def toggle_monitoring(self):
        if not self.worker.is_running:
            if not self.worker.target_rect:
                QtWidgets.QMessageBox.warning(self, "提示", "请先选取识别区域")
                return
            self.worker.is_running = True
            self.worker.start()
            self.monitor_btn.setText("⏹ 停止监控")
            self.monitor_btn.setStyleSheet("background-color: #e74c3c; color: white;")
        else:
            self.worker.is_running = False
            self.monitor_btn.setText("▶ 开始监控")
            self.monitor_btn.setStyleSheet("")

    def toggle_debug(self, checked):
        self.worker.show_debug = checked
        if not checked: self.preview_label.clear()

    def manual_debug_print(self):
        """点击按钮打印内存中最近一次识别详情"""
        res = self.worker._last_raw_results
        ts = time.strftime('%H:%M:%S')
        self.log_output.appendPlainText(f"\n--- 手动快照调试 ({ts}) ---")
        if not res:
            self.log_output.appendPlainText("未发现任何内容。")
        else:
            for i, it in enumerate(res):
                self.log_output.appendPlainText(f"块[{i}]: '{it[1]}' (置信度:{it[2]:.4f})")
        self.log_output.moveCursor(QtGui.QTextCursor.MoveOperation.End)

    def update_ui(self, text, conf, qimg, raw_results):
        # 更新状态文字颜色
        if not text:
            self.result_display.setText("无数据")
            self.result_display.setStyleSheet("color: #666; background: black; font-size: 50px;")
        elif text == "0":
            self.result_display.setText("0")
            self.result_display.setStyleSheet("color: #00FF00; background: black; font-size: 50px;")
        else:
            self.result_display.setText(text)
            self.result_display.setStyleSheet("color: #FF0000; background: black; font-size: 50px;")

        # 更新预览
        if self.worker.show_debug:
            self.preview_label.setPixmap(QtGui.QPixmap.fromImage(qimg).scaled(
                self.preview_label.size(), QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation))

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())