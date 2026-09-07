"""Dark, friendly stylesheet."""

ACCENT = "#ff3fb4"
ACCENT2 = "#33e6ff"

STYLESHEET = f"""
QWidget {{ background: #15161c; color: #e8e9f0; font-size: 13px; font-family: 'Segoe UI', 'Inter', 'Helvetica Neue', Arial, sans-serif; }}
QMainWindow, QDialog {{ background: #15161c; }}
QLabel#title {{ font-size: 26px; font-weight: 700; color: #ffffff; }}
QLabel#tagline {{ font-size: 13px; color: {ACCENT}; font-style: italic; }}
QLabel#h2 {{ font-size: 16px; font-weight: 600; color: #ffffff; margin-top: 6px; }}
QLabel#muted {{ color: #9aa0b4; }}
QLabel#status_ok {{ color: #4ade80; }}
QLabel#status_bad {{ color: #f87171; }}
QFrame#card {{ background: #1e2029; border: 1px solid #2c2f3d; border-radius: 10px; }}
QTabWidget::pane {{ border: 1px solid #2c2f3d; border-radius: 8px; top: -1px; }}
QTabBar::tab {{ background: #1a1b23; padding: 9px 18px; border: 1px solid #2c2f3d; border-bottom: none; border-top-left-radius: 8px; border-top-right-radius: 8px; margin-right: 2px; color: #b8bccc; }}
QTabBar::tab:selected {{ background: #23252f; color: #ffffff; }}
QPushButton {{ background: #2a2d3a; border: 1px solid #3a3e50; border-radius: 7px; padding: 7px 14px; color: #f0f1f7; }}
QPushButton:hover {{ background: #343849; }}
QPushButton:pressed {{ background: #1f2230; }}
QPushButton:disabled {{ color: #6b7086; background: #202230; }}
QPushButton#primary {{ background: {ACCENT}; border: none; color: #ffffff; font-weight: 700; font-size: 15px; padding: 12px 24px; }}
QPushButton#primary:hover {{ background: #ff5fc2; }}
QPushButton#primary:checked {{ background: #22c55e; }}
QPushButton#danger {{ background: #7f1d1d; border: none; }}
QPushButton#accent2 {{ background: #0e6b78; border: none; }}
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ background: #0f1015; border: 1px solid #33364a; border-radius: 6px; padding: 5px 8px; selection-background-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: #1e2029; selection-background-color: #343849; }}
QTableWidget {{ background: #0f1015; gridline-color: #2c2f3d; border: 1px solid #2c2f3d; border-radius: 6px; }}
QHeaderView::section {{ background: #1e2029; padding: 6px; border: none; border-bottom: 1px solid #2c2f3d; color: #b8bccc; }}
QTableWidget::item:selected {{ background: #3b2a45; }}
QProgressBar {{ background: #0f1015; border: 1px solid #33364a; border-radius: 6px; text-align: center; height: 16px; }}
QProgressBar::chunk {{ background: {ACCENT2}; border-radius: 5px; }}
QProgressBar#gauge::chunk {{ background: {ACCENT}; }}
QCheckBox::indicator {{ width: 16px; height: 16px; }}
QStatusBar {{ background: #101116; color: #9aa0b4; }}
QScrollArea {{ border: none; }}
QToolTip {{ background: #1e2029; color: #e8e9f0; border: 1px solid #3a3e50; }}
QSplitter::handle {{ background: #2c2f3d; }}
"""
