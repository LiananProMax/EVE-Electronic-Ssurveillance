import sys
import cv2
import numpy as np
import time
import winsound
from PyQt6 import QtWidgets, QtCore, QtGui
from PIL import ImageGrab
import easyocr

# 强制禁用 opencv 的多线程，防止冲突
cv2.setNumThreads(0)

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
                            ratio = w / (h if h > 0 else 1)
                            
                            # 过滤干扰项
                            if conf < 0.35 and ratio < 0.15: continue
                            
                            if conf > 0.25: # 降低一点点门槛，确保预览能看到
                                all_nums.append(text)
                                conf_sum += conf
                                valid_results.append(res)
                                if text != "0": should_alarm = True
                    
                    display_text = "".join(all_nums) if all_nums else ""
                    avg_conf = conf_sum / len(valid_results) if valid_results else 0.0
                    
                    # 核心改动：无论是否报警，都生成带有标注的图片
                    qimg = self.process_debug_img(img_np, valid_results)
                    self.result_ready.emit(display_text, avg_conf, qimg, results)
                    
                    if should_alarm:
                        winsound.Beep(1000, 500)
                except Exception as e:
                    print(f"识别异常: {e}")
            
            time.sleep(0.5)

    def process_debug_img(self, img_np, results):
        """增强版绘图逻辑：在图片上画框并标注文字"""
        img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
        
        if results:
            for res in results:
                # 还原坐标 (识别时放大了2倍)
                pos = np.array(res[0], np.int32) // 2 
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
        
        # 保存当前预览图像，用于窗口缩放时重新渲染
        self.current_preview_image = None
        
        self.init_ui()
        self.apply_styles()

    def init_ui(self):
        # 主布局
        self.main_layout = QtWidgets.QVBoxLayout(self)
        self.main_layout.setSpacing(15)
        self.main_layout.setContentsMargins(20, 20, 20, 20)

        # 1. 核心数值显示区 (大卡片)
        self.display_card = QtWidgets.QFrame()
        self.display_card.setObjectName("DisplayCard")
        display_layout = QtWidgets.QVBoxLayout(self.display_card)
        display_layout.setContentsMargins(20, 15, 20, 15)
        
        self.status_title = QtWidgets.QLabel("系统就绪")
        self.status_title.setStyleSheet("font-size: 12px; color: #888; letter-spacing: 2px;")
        self.status_title.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        
        self.result_display = QtWidgets.QLabel("待机")
        self.result_display.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.result_display.setObjectName("BigNumber")
        
        display_layout.addWidget(self.status_title)
        display_layout.addWidget(self.result_display)
        self.main_layout.addWidget(self.display_card)

        # 2. 实时预览区
        self.preview_label = QtWidgets.QLabel("实时流已限制")
        self.preview_label.setMinimumSize(400, 200)
        self.preview_label.setObjectName("PreviewWindow")
        self.preview_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setScaledContents(False)
        # 设置大小策略：水平和垂直都可扩展
        self.preview_label.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding, 
            QtWidgets.QSizePolicy.Policy.Expanding
        )
        self.main_layout.addWidget(self.preview_label, 1)  # 添加拉伸因子

        # 3. 控制面板区 (并排布局)
        control_group = QtWidgets.QHBoxLayout()
        control_group.setSpacing(10)
        
        # 预览开关按钮
        self.debug_btn = QtWidgets.QPushButton("实时预览")
        self.debug_btn.setCheckable(True)
        self.debug_btn.setFixedHeight(38)
        
        control_group.addWidget(self.debug_btn, 1)
        self.main_layout.addLayout(control_group)

        # 4. 操作按钮区
        btn_grid = QtWidgets.QGridLayout()
        btn_grid.setSpacing(10)
        
        self.select_btn = QtWidgets.QPushButton("选择区域")
        self.select_btn.setFixedHeight(42)
        
        self.print_btn = QtWidgets.QPushButton("调试快照")
        self.print_btn.setFixedHeight(42)
        
        self.monitor_btn = QtWidgets.QPushButton("开始监控")
        self.monitor_btn.setObjectName("PrimaryBtn")
        self.monitor_btn.setFixedHeight(50)
        
        btn_grid.addWidget(self.select_btn, 0, 0)
        btn_grid.addWidget(self.print_btn, 0, 1)
        btn_grid.addWidget(self.monitor_btn, 1, 0, 1, 2) # 跨两列
        self.main_layout.addLayout(btn_grid)

        # 5. 日志区
        self.log_output = QtWidgets.QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setPlaceholderText("系统日志将显示在此...")
        self.log_output.setMinimumHeight(100)
        self.log_output.setMaximumHeight(200)
        # 设置大小策略：可以垂直扩展
        self.log_output.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding, 
            QtWidgets.QSizePolicy.Policy.Expanding
        )
        self.main_layout.addWidget(self.log_output)

        # 绑定事件
        self.select_btn.clicked.connect(self.start_selection)
        self.monitor_btn.clicked.connect(self.toggle_monitoring)
        self.print_btn.clicked.connect(self.manual_debug_print)

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
        """应用现代化深色主题样式"""
        self.setStyleSheet("""
            QWidget {
                background-color: #1A1A1A;
                color: #E0E0E0;
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
            }
            
            #DisplayCard {
                background-color: #252525;
                border: 1px solid #333;
                border-radius: 12px;
            }
            
            #BigNumber {
                font-size: 72px;
                font-weight: 800;
                color: #00FFCC;
                background: transparent;
                margin: 10px 0;
            }
            
            #PreviewWindow {
                background-color: #000;
                border: 2px solid #333;
                border-radius: 8px;
                color: #555;
                font-size: 13px;
            }
            
            QPushButton {
                background-color: #333;
                border: none;
                border-radius: 6px;
                padding: 8px;
                font-weight: bold;
                font-size: 13px;
            }
            
            QPushButton:hover {
                background-color: #444;
            }
            
            QPushButton:pressed {
                background-color: #222;
            }
            
            QPushButton:checked {
                background-color: #0078D4;
                color: white;
            }
            
            #PrimaryBtn {
                background-color: #0078D4;
                font-size: 15px;
                font-weight: bold;
                margin-top: 5px;
            }
            
            #PrimaryBtn:hover {
                background-color: #2B88D8;
            }
            
            #PrimaryBtn:pressed {
                background-color: #005A9E;
            }
            
            QComboBox {
                background-color: #333;
                border: 1px solid #444;
                border-radius: 6px;
                padding: 8px 10px;
                font-size: 13px;
            }
            
            QComboBox:hover {
                border: 1px solid #555;
            }
            
            QComboBox::drop-down {
                border: none;
                width: 30px;
            }
            
            QComboBox::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 6px solid #E0E0E0;
                margin-right: 8px;
            }
            
            QComboBox QAbstractItemView {
                background-color: #2A2A2A;
                border: 1px solid #444;
                selection-background-color: #0078D4;
                outline: none;
            }
            
            QPlainTextEdit {
                background-color: #0F0F0F;
                border: 1px solid #222;
                border-radius: 6px;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 11px;
                color: #888;
                padding: 8px;
            }
        """)

    def get_reader(self):
        m_key = "CPU"
        if self.readers[m_key] is None:
            self.log_output.appendPlainText("⏳ 正在加载 CPU 引擎...")
            QtWidgets.QApplication.setOverrideCursor(QtGui.QCursor(QtCore.Qt.CursorShape.WaitCursor))
            try:
                # 强制仅使用 CPU
                self.readers[m_key] = easyocr.Reader(['en'], gpu=False)
                self.log_output.appendPlainText("✅ CPU 引擎已就绪。")
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
            self.monitor_btn.setStyleSheet("""
                background-color: #CC3300;
                font-size: 15px;
                font-weight: bold;
                margin-top: 5px;
                border: none;
                border-radius: 6px;
            """)
            self.start_breathing_animation()
        else:
            self.worker.is_running = False
            self.monitor_btn.setText("开始监控")
            self.monitor_btn.setStyleSheet("") # 恢复默认
            self.stop_breathing_animation()

    def start_breathing_animation(self):
        """启动呼吸灯动画"""
        self.breathing_animation = QtCore.QPropertyAnimation(self.monitor_btn, b"styleSheet")
        self.breathing_animation.setDuration(2000)
        self.breathing_animation.setLoopCount(-1) # 无限循环
        
        # 关键帧动画
        self.breathing_animation.setKeyValueAt(0, """
            background-color: #CC3300;
            font-size: 15px;
            font-weight: bold;
            margin-top: 5px;
            border: none;
            border-radius: 6px;
        """)
        self.breathing_animation.setKeyValueAt(0.5, """
            background-color: #FF4422;
            font-size: 15px;
            font-weight: bold;
            margin-top: 5px;
            border: none;
            border-radius: 6px;
        """)
        self.breathing_animation.setKeyValueAt(1.0, """
            background-color: #CC3300;
            font-size: 15px;
            font-weight: bold;
            margin-top: 5px;
            border: none;
            border-radius: 6px;
        """)
        self.breathing_animation.start()
    
    def stop_breathing_animation(self):
        """停止呼吸灯动画"""
        if self.breathing_animation:
            self.breathing_animation.stop()
            self.breathing_animation = None

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
        
        # 判定报警状态
        is_alert = any(c != '0' for c in text) if text else False
        
        # 现代化的颜色切换：使用更亮的霓虹色
        if is_alert:
            self.result_display.setStyleSheet("""
                color: #FF3366;
                font-size: 72px;
                font-weight: 800;
                background: transparent;
                margin: 10px 0;
            """)
            self.status_title.setText("⚠️  检测到警报")
            self.status_title.setStyleSheet("color: #FF3366; font-size: 12px; letter-spacing: 2px;")
        else:
            self.result_display.setStyleSheet("""
                color: #00FFCC;
                font-size: 72px;
                font-weight: 800;
                background: transparent;
                margin: 10px 0;
            """)
            self.status_title.setText("系统扫描中")
            self.status_title.setStyleSheet("color: #00FFCC; font-size: 12px; letter-spacing: 2px;")
        
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