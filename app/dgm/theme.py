LIGHT = """
QMainWindow,QWidget{background:#f5f4f0;color:#283747;font-size:13px;}
QLabel#pageTitle{font-size:19px;font-weight:700;color:#264d6b;background:transparent;}
QLabel#subTitle{font-size:12px;color:#596d80;background:transparent;}
QLabel#miniTitle{font-size:12px;font-weight:700;color:#385b75;background:transparent;}
QFrame#reviewCard{background:#ffffff;border:1px solid #cddbe5;border-radius:8px;}
QFrame#reviewCard QLabel{background:transparent;border:0;}
QLabel#cardValue{font-size:22px;font-weight:700;color:#23506e;}
QLabel#cardCaption{font-size:10px;font-weight:600;color:#536e82;}
QLabel#cardNote{font-size:10px;color:#647786;}
QLabel#safeBanner{background:#e9f4ef;color:#29634d;padding:8px;border:1px solid #bfdacb;border-radius:6px;}
QLabel#warningBanner{background:#fff3db;color:#7b571c;padding:8px;border:1px solid #e4d2ad;border-radius:6px;}
QLineEdit,QComboBox,QPlainTextEdit,QSpinBox,QDoubleSpinBox{background:#ffffff;border:1px solid #cad3dd;border-radius:6px;padding:5px;selection-background-color:#d7e8fa;selection-color:#152d46;}
QPushButton,QToolButton{background:rgba(255,255,255,235);border:1px solid #c9d4df;border-radius:6px;padding:6px 10px;color:#294562;font-weight:600;}
QPushButton:hover,QToolButton:hover{background:#eaf1f8;border-color:#87aaca;} QPushButton:disabled{color:#98a2b3;background:#eeeff1;}
QPushButton[actionRole="primary"]{background:#1768b0;color:#ffffff;border-color:#12558f;font-weight:700;}
QPushButton[actionRole="primary"]:hover{background:#125993;border-color:#0f4877;}
QPushButton[actionRole="primary"]:pressed{background:#0d4679;}
QPushButton[actionRole="compare"]{background:#e0f2eb;color:#17634c;border-color:#a6cebe;}
QPushButton[actionRole="compare"]:hover{background:#cce8dc;border-color:#78b49b;}
QPushButton[actionRole="compare"]:pressed{background:#b8dccd;}
QPushButton[actionRole="danger"]{background:#fff2f1;color:#a53b37;border-color:#e3b5b2;}
QPushButton[actionRole="danger"]:hover{background:#fbe1df;border-color:#d2908b;}
QPushButton[actionRole="danger"]:pressed{background:#f3cac6;}
QPushButton[actionRole="stop"]{background:#fff1df;color:#8b4e19;border-color:#dfbb8f;}
QPushButton[actionRole="stop"]:hover{background:#ffe4c3;border-color:#c9985d;}
QPushButton[actionRole]:disabled{background:#edf0f3;color:#8c98a5;border-color:#d3dae1;}
QPushButton:focus{border-color:#356fa5;}
QWidget#scanConfiguration{background:#eaf1fa;border-left:3px solid #5d90c5;border-radius:5px;}
QWidget#comparisonControls{background:#e7f2ed;border-left:3px solid #56a389;border-radius:5px;}
QWidget#scanConfiguration QLabel{background:transparent;}
QWidget#scanOptions{background:transparent;}
QWidget#comparisonControls QLabel{background:transparent;font-weight:600;color:#2e6352;}
QWidget#resultFilters{background:#f0f3f7;border:1px solid #dce3e9;border-radius:6px;}
QWidget#resultFilters QLabel{background:transparent;border:0;}
QLabel#zoneTag{font-size:11px;font-weight:700;color:#516981;padding:0 4px;}
QTableView,QTableWidget,QTreeWidget{background:#ffffff;alternate-background-color:#f4f7fa;border:1px solid #ccd6df;border-radius:5px;selection-background-color:#dfebf7;selection-color:#203c59;gridline-color:#e2e7ed;}
QHeaderView::section{background:#eaf0f5;color:#314d68;padding:8px;border:0;border-right:1px solid #d9e2eb;}
QTabBar::tab{padding:10px 16px;background:#eeeff0;color:#536477;border:1px solid #d5dce3;border-bottom:0;border-top-left-radius:6px;border-top-right-radius:6px;}
QTabBar::tab:selected{background:#e5effb;color:#155c98;font-weight:600;}
QTabBar#comparisonSections::tab:selected{background:#e0f2eb;color:#17634c;}
QTabBar#comparisonSections::tab:last:selected{background:#fff0d9;color:#795815;}
QTabWidget::pane{border:1px solid #d5dce3;background:#f7f8fa;}
QGroupBox{background:#eff4f8;border:1px solid #d5dce3;border-radius:7px;margin-top:10px;padding-top:10px;} QGroupBox::title{subcontrol-origin:margin;left:10px;color:#314d68;}
QGroupBox QLabel,QGroupBox QCheckBox{background:transparent;}
QCheckBox::indicator{width:15px;height:15px;border:1px solid #95aabd;border-radius:3px;background:#ffffff;}
QCheckBox::indicator:checked{background:#438ccc;border:1px solid #236aab;}
QLabel#metric{background:rgba(255,255,255,238);border:1px solid #d8e0e8;border-radius:8px;padding:10px;color:#314760;}
QWidget#metricCard{border:1px solid #ccd9e4;border-radius:9px;background:#edf2f6;}
QWidget#metricCard[tone="new"]{background:#e7f2ff;border-color:#bed7f1;}
QWidget#metricCard[tone="delta"]{background:#e8f4ee;border-color:#bddbcc;}
QWidget#metricCard[tone="summary"]{background:#fff3df;border-color:#ead5ad;}
QWidget#metricCard QLabel{background:transparent;border:0;padding:0;}
QWidget#metricCard QWidget{background:transparent;}
QLabel#metricCaption{font-size:11px;font-weight:700;color:#425c73;}
QLabel#metricValue{font-size:20px;font-weight:800;color:#244b70;}
QWidget#metricCard[tone="summary"] QLabel#metricValue{font-size:12px;font-weight:600;}
QLabel#metricNote{font-size:10px;font-weight:400;color:#586b7a;}
QLabel#sectionTitle{font-size:13px;font-weight:600;background:#eaf0f6;border-radius:5px;padding:7px;color:#294d6c;}
QLabel#sectionTitle[zone="tree"]{background:#e3edf9;color:#24517d;border-left:3px solid #6297cb;}
QLabel#sectionTitle[zone="details"]{background:#e0f1eb;color:#25664e;border-left:3px solid #5aa58b;}
QWidget#paneBar{border:1px solid #cbdbe9;border-radius:6px;background:#e7eff9;}
QWidget#paneBar[zone="details"]{background:#e5f2ec;border-color:#c2dbd0;}
QWidget#paneBar QLabel,QWidget#paneBar QCheckBox{background:transparent;border:0;padding:0;color:#285777;}
QWidget#paneBar[zone="details"] QLabel,QWidget#paneBar[zone="details"] QCheckBox{color:#245f4d;}
QLabel#paneTitle{font-size:13px;font-weight:700;}
QFrame#comparisonLegend{background:#fffefa;border:1px solid #dce3e7;border-radius:6px;}
QLabel#legendNote{background:transparent;color:#536477;font-size:11px;border:0;}
QTableView#comparisonTable{gridline-color:#e1e6e8;}
QGroupBox::title{font-size:14px;font-weight:600;}
QHeaderView::section{font-weight:600;}
QToolButton#helpToggle{font-size:17px;font-weight:600;padding:3px 8px;color:#2470a8;}
QToolButton:checked{background:#dcecfb;border-color:#75a5d0;}
QScrollArea{border:0;background:transparent;} QSplitter::handle{background:#d6e0e9;width:7px;}
QWidget#scanCanvas QPushButton,QWidget#scanCanvas QToolButton{padding:3px 7px;}
QWidget#scanCanvas QLineEdit,QWidget#scanCanvas QComboBox,QWidget#scanCanvas QDoubleSpinBox{padding:3px 5px;}
QWidget#scanCanvas QLabel#sectionTitle{padding:4px;}
QToolTip{background:#fffceb;color:#263e55;border:1px solid #d4cfa8;padding:6px;}
QProgressBar{background:#ffffff;border:1px solid #cad5df;border-radius:6px;min-height:12px;text-align:center;color:#294562;}
QProgressBar::chunk{background:#79aedd;border-radius:5px;}
"""
