import sys
import cv2
import numpy as np
import time
import winsound
import torch  
from PyQt6 import QtWidgets, QtCore, QtGui
from PIL import ImageGrab
import easyocr

# 强制禁用 opencv 的多线程，防止与 PyQt 冲突导致卡死
cv2.setNumThreads(0)

class Worker(QtCore.QThread):
    result_ready = QtCore.pyqtSignal(str, float, QtGui.QImage, list)
    
    def __init__(self):
        super().__init__()
        self.reader = None 
        self.target_rect = None 
        self.is_running = False
        self.show_debug = False
        self._last_raw_results = []

    def run(self):
        if not self.reader: return
        while self.is_running:
            if self.target_rect:
                try:
                    # 1. 截图 (物理像素)
                    img = ImageGrab.grab(bbox=self.target_rect, all_screens=True)
                    img_np = np.array(img)
                    
                    # 2. 图像增强预处理
                    gray = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)
                    gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
                    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
                    enhanced = clahe.apply(gray)
                    _, thresh = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                    
                    # 3. OCR 识别
                    results = self.reader.readtext(thresh, allowlist='0123456789')
                    self._last_raw_results = results 

                    all_nums, conf_sum, valid_results, should_alarm = [], 0, [], False
                    
                    if results:
                        for res in results:
                            pos, text, conf = res[0], res[1], res[2]
                            w = abs(pos[1][0] - pos[0][0])
                            h = abs(pos[2][1] - pos[1][1])
                            ratio = w / h if h > 0 else 0
                            
                            # 过滤感叹号等窄条干扰
                            if conf < 0.45 and ratio < 0.15: continue
                            
                            if conf > 0.3:
                                all_nums.append(text)
                                conf_sum += conf
                                valid_results.append(res)
                                if text != "0": should_alarm = True
                    
                    display_text = "".join(all_nums) if all_nums else ""
                    avg_conf = conf_sum / len(valid_results) if valid_results else 0.0
                    
                    qimg = self.process_debug_img(img_np, valid_results, should_alarm)
                    self.result_ready.emit(display_text, avg_conf, qimg, results)
                    
                    if should_alarm:
                        winsound.Beep(1000, 500)
                except Exception as e:
                    print(f"识别异常: {e}")
            
            time.sleep(0.5)

    def process_debug_img(self, img_np, results, triggered):
        img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
        color = (0, 0, 255) if triggered else (0, 255, 0)
        if self.show_debug and results:
            for res in results:
                pos = np.array(res[0], np.int32) // 2 
                cv2.polylines(img_bgr, [pos], True, color, 2)
                cv2.putText(img_bgr, res[1], (pos[0][0], pos[0][1] - 5), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        h, w, ch = img_bgr.shape
        return QtGui.QImage(img_bgr.data, w, h, w * ch, QtGui.QImage.Format.Format_BGR888).copy()

class MainWindow(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("数字监控报警 Pro (CUDA 加速版)")
        self.setFixedSize(500, 720)
        
        self.screen_ratio = self.devicePixelRatio()
        self.readers = {"CPU": None, "GPU": None}
        self.worker = Worker()
        self.worker.result_ready.connect(self.update_ui)
        self.init_ui()

    def init_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        self.result_display = QtWidgets.QLabel("等待开始")
        self.result_display.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.result_display.setStyleSheet("font-size: 50px; font-weight: bold; color: #00FF00; background: black; border-radius: 10px; min-height: 120px;")
        layout.addWidget(self.result_display)

        self.preview_label = QtWidgets.QLabel("预览窗口")
        self.preview_label.setFixedSize(480, 160)
        self.preview_label.setStyleSheet("border: 2px dashed #666; background: #333; color: #eee;")
        self.preview_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.preview_label)

        mode_layout = QtWidgets.QHBoxLayout()
        mode_layout.addWidget(QtWidgets.QLabel("运行模式:"))
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(["CPU 模式 (默认稳定)", "GPU 模式 (CUDA 加速)"])
        mode_layout.addWidget(self.mode_combo)
        layout.addLayout(mode_layout)

        self.log_output = QtWidgets.QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setStyleSheet("background: #f8f8f8; font-family: Consolas;")
        layout.addWidget(self.log_output)

        grid = QtWidgets.QGridLayout()
        self.select_btn = QtWidgets.QPushButton("🔍 1. 选取区域")
        self.monitor_btn = QtWidgets.QPushButton("▶ 2. 开始监控")
        self.debug_btn = QtWidgets.QPushButton("🛠 开启预览")
        self.debug_btn.setCheckable(True)
        self.print_btn = QtWidgets.QPushButton("📸 调试快照")
        
        grid.addWidget(self.select_btn, 0, 0); grid.addWidget(self.monitor_btn, 0, 1)
        grid.addWidget(self.debug_btn, 1, 0); grid.addWidget(self.print_btn, 1, 1)
        layout.addLayout(grid)

        self.select_btn.clicked.connect(self.start_selection)
        self.monitor_btn.clicked.connect(self.toggle_monitoring)
        self.print_btn.clicked.connect(self.manual_debug_print)

    def get_reader(self):
        is_gpu = self.mode_combo.currentIndex() == 1
        m_key = "GPU" if is_gpu else "CPU"
        
        if is_gpu and not torch.cuda.is_available():
            self.log_output.appendPlainText("❌ 警告：CUDA 环境不可用，切回 CPU。")
            self.mode_combo.setCurrentIndex(0)
            return self.get_reader()
        
        if self.readers[m_key] is None:
            self.log_output.appendPlainText(f"⏳ 正在加载 {m_key} 引擎...")
            QtWidgets.QApplication.setOverrideCursor(QtGui.QCursor(QtCore.Qt.CursorShape.WaitCursor))
            try:
                # 显式指定识别器
                self.readers[m_key] = easyocr.Reader(['en'], gpu=is_gpu)
                self.log_output.appendPlainText(f"✅ {m_key} 引擎就绪。")
            except Exception as e:
                self.log_output.appendPlainText(f"❌ 加载失败: {e}")
                self.mode_combo.setCurrentIndex(0)
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
        self.log_output.appendPlainText("🎯 监控区域已更新")

    def toggle_monitoring(self):
        if not self.worker.is_running:
            reader = self.get_reader()
            if not reader: return
            self.worker.reader = reader
            self.worker.is_running = True
            self.worker.start()
            self.monitor_btn.setText("⏹ 停止监控")
            self.mode_combo.setEnabled(False)
        else:
            self.worker.is_running = False
            self.monitor_btn.setText("▶ 开始监控")
            self.mode_combo.setEnabled(True)

    def manual_debug_print(self):
        res = self.worker._last_raw_results
        self.log_output.appendPlainText(f"\n--- 快照调试 ({time.strftime('%H:%M:%S')}) ---")
        if not res: self.log_output.appendPlainText("无内容")
        else:
            for i, it in enumerate(res):
                self.log_output.appendPlainText(f"块[{i}]: '{it[1]}' (置信度:{it[2]:.4f})")

    def update_ui(self, text, conf, qimg, raw):
        self.result_display.setText(text if text else "0")
        color = "#00FF00" if (not text or text == "0") else "#FF0000"
        self.result_display.setStyleSheet(f"color: {color}; background: black; font-size: 50px; font-weight: bold; border-radius: 10px;")
        if self.debug_btn.isChecked():
            self.preview_label.setPixmap(QtGui.QPixmap.fromImage(qimg).scaled(self.preview_label.size(), QtCore.Qt.AspectRatioMode.KeepAspectRatio))

if __name__ == "__main__":
    # 修正 PyQt6 的 DPI 设置
    app = QtWidgets.QApplication(sys.argv)
    app.setHighDpiScaleFactorRoundingPolicy(QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())