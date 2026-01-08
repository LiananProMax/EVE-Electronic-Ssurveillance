MAIN_STYLESHEET = """
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
"""

