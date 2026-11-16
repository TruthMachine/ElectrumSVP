import sys
from enum import Enum

from PyQt5.QtGui import QColor, QCursor, QPalette
from PyQt5.QtWidgets import QApplication, QWidget, QDialog, QMainWindow
from PyQt5.QtCore import QEvent, QObject, QTimer, pyqtSignal

class Theme(Enum):
    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"
    PINK = "pink"
    ORANGE = "orange"


def theme_preference_from_config(config, dark_default: bool = True) -> Theme:
    saved_theme = config.get("theme", None)

    if saved_theme == Theme.SYSTEM.value:
        return Theme.SYSTEM

    if saved_theme == Theme.DARK.value:
        return Theme.DARK

    if saved_theme == Theme.PINK.value:
        return Theme.PINK

    if saved_theme == Theme.ORANGE.value:
        return Theme.ORANGE

    if saved_theme == Theme.LIGHT.value:
        return Theme.LIGHT

    # Backward compatibility with existing ElectrumSVP configurations.
    dark_mode = config.get("dark_mode", dark_default)
    return Theme.DARK if dark_mode else Theme.LIGHT


def _uses_appkit():
    return sys.platform == 'darwin' and QApplication.platformName() == 'cocoa'


def system_theme() -> Theme:
    if _uses_appkit():
        from .macos_appearance import is_dark
        return Theme.DARK if is_dark() else Theme.LIGHT
    app = QApplication.instance()
    palette = app.style().standardPalette() if app else QPalette()
    return Theme.DARK if palette.color(QPalette.Window).lightness() < 128 else Theme.LIGHT


def resolve_theme(theme: Theme) -> Theme:
    return system_theme() if theme == Theme.SYSTEM else theme


def theme_from_config(config, dark_default: bool = True) -> Theme:
    return resolve_theme(theme_preference_from_config(config, dark_default))


class ThemeController(QObject):
    changed = pyqtSignal(object)

    def __init__(self, app, preference):
        super().__init__(app)
        self.app = app
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.refresh_system_theme)
        self.select(preference)

    def select(self, preference):
        self.preference = preference
        # Apply the native override before resolving System; nil restores AppKit's
        # effective system appearance rather than reading our previous override.
        if _uses_appkit():
            from .macos_appearance import set_appearance
            set_appearance(None if preference == Theme.SYSTEM else preference == Theme.DARK)
        self.effective = resolve_theme(preference)
        apply_theme(self.app, self.effective)
        self.changed.emit(self.effective)
        if preference == Theme.SYSTEM:
            self._timer.start()
        else:
            self._timer.stop()

    def refresh_system_theme(self):
        if self.preference == Theme.SYSTEM and system_theme() != self.effective:
            self.select(Theme.SYSTEM)


LIGHT_THEME = """
    PasswordLineEdit {
        background-color: transparent;
        color: #333333;
    }

    PasswordLineEdit QLineEdit,
    ButtonsLineEdit {
        background-color: #ffffff;
        color: #222222;
        border: 1px solid #aaaaaa;
        selection-background-color: #cce5ff;
        selection-color: #000000;
    }

    QTableView,
    QTreeView,
    QListView,
    QListWidget {
        background-color: #ffffff;
        color: #222222;
        alternate-background-color: #f5f8fa;
        gridline-color: #e3e2e2;
        border: 1px solid #e3e2e2;
        selection-background-color: #d3ebff;
        selection-color: #000000;
    }

"""


DARK_THEME = """
    /* ==================================================
       BASE / WINDOWS
       ================================================== */
    QWidget,
    QMainWindow,
    QDialog,
    QFrame,
    QGroupBox {
        background-color: #202124;
        color: #e8eaed;
    }

    QLabel {
        background-color: transparent;
        color: #e8eaed;
    }

    QWizard,
    QWizardPage {
        background-color: #202124;
        color: #e8eaed;
    }

    /* ==================================================
       INPUTS
       ================================================== */
    QLineEdit,
    QTextEdit,
    QPlainTextEdit,
    QSpinBox,
    QDoubleSpinBox,
    QComboBox {
        background-color: #303236;
        color: #f0f1f2;
        border: 1px solid #555960;
        border-radius: 3px;
        padding: 3px;
        selection-background-color: #607d9c;
        selection-color: #ffffff;
    }

    QLineEdit:focus,
    QTextEdit:focus,
    QPlainTextEdit:focus,
    QSpinBox:focus,
    QDoubleSpinBox:focus,
    QComboBox:focus {
        border: 1px solid #7aa7d9;
    }

    QLineEdit:disabled,
    QTextEdit:disabled,
    QPlainTextEdit:disabled,
    QSpinBox:disabled,
    QDoubleSpinBox:disabled,
    QComboBox:disabled {
        background-color: #252629;
        color: #777b82;
    }

    /* ==================================================
       COMBO BOX POPUPS
       ================================================== */
    QComboBox QAbstractItemView {
        background-color: #303236;
        color: #f0f1f2;
        border: 1px solid #454950;
        selection-background-color: #526b88;
        selection-color: #ffffff;
        outline: none;
    }

    QComboBox QAbstractItemView::item {
        background-color: #303236;
        color: #f0f1f2;
        padding: 4px;
    }

    QComboBox QAbstractItemView::item:selected {
        background-color: #526b88;
        color: #ffffff;
    }

    /* ==================================================
       TOOLBARS / ACCOUNT BAR
       ================================================== */
    QToolBar {
        background-color: #202124;
        border: none;
        spacing: 0px;
    }

    QToolBar::separator {
        background-color: #202124;
        width: 0px;
        height: 0px;
    }

    QToolButton {
        background-color: #202124;
        color: #e8eaed;
        border: none;
        margin: 0px;
        padding: 0px;
    }

    QToolButton:hover {
        background-color: #303236;
    }

    QToolButton:pressed,
    QToolButton:checked {
        background-color: #454950;
    }

    /* ==================================================
       BUTTONS
       ================================================== */
    QPushButton {
        background-color: #303236;
        color: #f0f1f2;
        border: 1px solid #555960;
        border-radius: 3px;
        padding: 5px 10px;
    }

    QPushButton:hover {
        background-color: #3c4045;
    }

    QPushButton:pressed {
        background-color: #454950;
    }

    QPushButton:disabled {
        background-color: #252629;
        color: #777b82;
        border-color: #3c3f43;
    }

    /* ==================================================
       TABLES / TREES / LISTS
       ================================================== */
    QTableView,
    QTreeView,
    QListView,
    QListWidget {
        background-color: #303236;
        color: #f1f2f3;
        alternate-background-color: #383b40;
        gridline-color: #454950;
        border: 1px solid #454950;
        selection-background-color: #526b88;
        selection-color: #ffffff;
    }

    QTableView::item,
    QTreeView::item,
    QListView::item,
    QListWidget::item {
        background-color: #303236;
        color: #f1f2f3;
    }

    QTableView::item:alternate,
    QTreeView::item:alternate,
    QListView::item:alternate,
    QListWidget::item:alternate {
        background-color: #383b40;
        color: #f1f2f3;
    }

    QTableView::item:selected,
    QTreeView::item:selected,
    QListView::item:selected,
    QListWidget::item:selected {
        background-color: #526b88;
        color: #ffffff;
    }

    QTableView::item:hover,
    QTreeView::item:hover,
    QListView::item:hover,
    QListWidget::item:hover {
        background-color: #41454b;
        color: #ffffff;
    }

    QTableView::indicator,
    QTreeView::indicator,
    QListView::indicator {
        background-color: #303236;
        border: 1px solid #666b72;
    }

    QTableView::indicator:checked,
    QTreeView::indicator:checked,
    QListView::indicator:checked {
        background-color: #ff8800;
        border: 1px solid #7d9fca;
    }

    /* ==================================================
       TABLE HEADERS
       ================================================== */
    QHeaderView {
        background-color: #292b2f;
    }

    QHeaderView::section {
        background-color: #303236;
        color: #e8eaed;
        border: 1px solid #454950;
        padding: 4px;
    }

    QTableCornerButton::section {
        background-color: #303236;
        border: 1px solid #454950;
    }

    QAbstractScrollArea {
        background-color: #303236;
    }

    /* ==================================================
       UTXO / TREE ITEMS
       ================================================== */
    QTreeWidget {
        background-color: #303236;
        color: #f1f2f3;
    }

    QTreeWidget::item {
        background: none;
        color: #f1f2f3;
    }

    QTreeWidget::item:selected {
        background-color: #526b88;
        color: #ffffff;
    }

    /* ==================================================
       TABS
       ================================================== */
    QTabWidget::pane {
        background-color: #202124;
        border: 1px solid #3c4043;
    }

    QTabBar {
        background-color: #202124;
    }

    QTabBar::tab {
        background-color: #303236;
        color: #d0d3d7;
        padding: 6px 12px;
        border: 1px solid #3c4043;
    }

    QTabBar::tab:selected {
        background-color: #454950;
        color: #ffffff;
    }

    QTabBar::tab:hover {
        background-color: #3a3d42;
        color: #ffffff;
    }

    /* ==================================================
       MENUS
       ================================================== */
    QMenu {
        background-color: #303236;
        color: #e8eaed;
        border: 1px solid #555960;
    }

    QMenu::item {
        background-color: transparent;
        color: #e8eaed;
        padding: 5px 12px;
    }

    QMenu::item:selected {
        background-color: #454950;
        color: #ffffff;
    }

    /* ==================================================
       CHECKBOXES / RADIO BUTTONS
       ================================================== */
    QCheckBox,
    QRadioButton {
        background-color: transparent;
        color: #e8eaed;
    }

    QCheckBox::indicator,
    QRadioButton::indicator {
        width: 14px;
        height: 14px;
        background-color: #303236;
        border: 1px solid #666b72;
        border-radius: 2px;
    }

    QCheckBox::indicator:checked,
    QRadioButton::indicator:checked {
        background-color: #ff8800;
        border: 1px solid #7d9fca;
    }

    QCheckBox::indicator:unchecked:hover,
    QRadioButton::indicator:unchecked:hover {
        background-color: #41454b;
        border: 1px solid #7d838b;
    }

    /* ==================================================
       PASSWORD DIALOG
       ================================================== */
    PasswordLineEdit {
        background-color: #303236;
        color: #f1f2f3;
    }

    PasswordLineEdit QLineEdit,
    ButtonsLineEdit {
        background-color: #3a3d42;
        color: #f1f2f3;
        border: 1px solid #5b5f66;
        selection-background-color: #607d9c;
        selection-color: #ffffff;
    }

    PasswordLineEdit QPushButton,
    ButtonsLineEdit QPushButton {
        background-color: #3a3d42;
        color: #e2e4e7;
        border: 1px solid #5b5f66;
    }

    /* ==================================================
       FORM SECTIONS
       ================================================== */
    #FormFrame {
        background-color: #303236;
        border: 1px solid #454950;
    }

    #FormSectionLabel {
        background-color: transparent;
        color: #e2e4e7;
    }

    #FormSectionTitle {
        background-color: transparent;
        color: #e2e4e7;
    }

    #FormSeparatorLine {
        background-color: transparent;
        border: 1px solid #454950;
    }

    /* ==================================================
       TOOLTIPS
       ================================================== */
    QToolTip {
        background-color: #303236;
        color: #f0f1f2;
        border: 1px solid #555960;
    }

/* ==================================================
   SCROLLBARS
   ================================================== */
QScrollBar:vertical {
    background-color: #303236;
    width: 12px;
    margin: 0px;
    border: none;
}

QScrollBar::groove:vertical {
    background-color: #303236;
    border: none;
}

QScrollBar::handle:vertical {
    background-color: #454950;
    min-height: 30px;
    border-radius: 5px;
}

QScrollBar::handle:vertical:hover {
    background-color: #526b88;
}

QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {
    background-color: #303236;
    border: none;
}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {
    background-color: #303236;
    border: none;
    height: 0px;
}

QScrollBar:horizontal {
    background-color: #303236;
    height: 12px;
    margin: 0px;
    border: none;
}

QScrollBar::groove:horizontal {
    background-color: #303236;
    border: none;
}

QScrollBar::handle:horizontal {
    background-color: #454950;
    min-width: 30px;
    border-radius: 5px;
}

QScrollBar::handle:horizontal:hover {
    background-color: #526b88;
}

QScrollBar::add-page:horizontal,
QScrollBar::sub-page:horizontal {
    background-color: #303236;
    border: none;
}

QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal {
    background-color: #303236;
    border: none;
    width: 0px;
}
"""


PINK_THEME = """
    /* ==================================================
       BASE / WINDOWS
       ================================================== */
    QWidget,
    QMainWindow,
    QDialog,
    QFrame,
    QGroupBox {
        background-color: #e7bfd0;
        color: #30252c;
    }

    QLabel {
        background-color: transparent;
        color: #30252c;
    }

    /* ==================================================
       INPUTS
       ================================================== */
    QLineEdit,
    QTextEdit,
    QPlainTextEdit,
    QSpinBox,
    QDoubleSpinBox,
    QComboBox {
        background-color: #eed5df;
        color: #30252c;
        border: 1px solid #c79aaa;
        border-radius: 3px;
        padding: 3px;
        selection-background-color: #9e3f68;
        selection-color: #ffffff;
    }

    QLineEdit:focus,
    QTextEdit:focus,
    QPlainTextEdit:focus,
    QSpinBox:focus,
    QDoubleSpinBox:focus,
    QComboBox:focus {
        border: 1px solid #ae416b;
    }

    QLineEdit:disabled,
    QTextEdit:disabled,
    QPlainTextEdit:disabled,
    QSpinBox:disabled,
    QDoubleSpinBox:disabled,
    QComboBox:disabled {
        background-color: #d9b8c6;
        color: #8f7781;
    }

    /* ==================================================
       COMBO BOX POPUPS
       ================================================== */
    QComboBox QAbstractItemView {
        background-color: #f0d7e1;
        color: #30252c;
        border: 1px solid #c79aaa;
        selection-background-color: #9e3f68;
        selection-color: #ffffff;
        outline: none;
    }

    QComboBox QAbstractItemView::item {
        background-color: #f0d7e1;
        color: #30252c;
        padding: 4px;
    }

    QComboBox QAbstractItemView::item:selected {
        background-color: #9e3f68;
        color: #ffffff;
    }

    /* ==================================================
       TOOLBARS / ACCOUNT BAR
       ================================================== */
    QToolBar {
        background-color: #e7bfd0;
        border: none;
        spacing: 0px;
    }

    QToolBar::separator {
        background-color: #e7bfd0;
        width: 0px;
        height: 0px;
    }

    QToolBar QToolButton {
        background-color: transparent;
        color: #30252c;
        border: none;
        margin: 0px;
        padding: 0px;
    }

    QToolBar QToolButton:hover {
        background-color: #dc91ad;
        color: #30252c;
    }

    QToolBar QToolButton:pressed,
    QToolBar QToolButton:checked {
        background-color: #9e3f68;
        color: #ffffff;
    }



    /* ==================================================
       BUTTONS
       ================================================== */
    QPushButton {
        background-color: #f0d7e1;
        color: #30252c;
        border: 1px solid #c79aaa;
        border-radius: 3px;
        padding: 5px 10px;
    }

    QPushButton:hover {
        background-color: #dc91ad;
        border: 1px solid #ae416b;
    }

    QPushButton:pressed {
        background-color: #9e3f68;
        color: #ffffff;
    }

    QPushButton:disabled {
        background-color: #d9b8c6;
        color: #8f7781;
        border-color: #c7a4b3;
    }

    /* ==================================================
       TABLES / TREES / LISTS
       ================================================== */
    QTableView,
    QTreeView,
    QListView,
    QListWidget {
        background-color: #e1b8c9;
        color: #30252c;
        alternate-background-color: #d4aabd;
        gridline-color: #c59bab;
        border: 1px solid #c79aaa;
        selection-background-color: #9e3f68;
        selection-color: #ffffff;
    }

    QTableView::item,
    QListView::item,
    QListWidget::item {
        background: none;
        color: #30252c;
    }

    QTreeView::item {
        background: none;
        color: #30252c;
        padding: 0px 0px 0px 4px;
    }

    QTableView::item:alternate,
    QTreeView::item:alternate,
    QListView::item:alternate,
    QListWidget::item:alternate {
        background-color: #d4aabd;
        color: #30252c;
    }

    QTableView::item:selected,
    QTreeView::item:selected,
    QListView::item:selected,
    QListWidget::item:selected {
        background-color: #9e3f68;
        color: #ffffff;
    }

    QTableView::item:hover,
    QTreeView::item:hover,
    QListView::item:hover,
    QListWidget::item:hover {
        background-color: #dc91ad;
        color: #30252c;
    }

    /* ==================================================
       TABLES / TREES / LISTS
       ================================================== */
    QTableView,
    QTreeView,
    QListView,
    QListWidget {
        background-color: #d2a2b5;
        color: #30252c;
        alternate-background-color: #c79aaa;
        gridline-color: #bf8fa3;
        border: 1px solid #bf8fa3;
        selection-background-color: #9e3f68;
        selection-color: #ffffff;
    }

    QTableView::item,
    QListView::item,
    QListWidget::item {
        background: none;
        color: #30252c;
    }

    QTreeView::item {
        background: none;
        color: #30252c;
        padding: 0px 0px 0px 4px;
    }

    QTableView::item:alternate,
    QTreeView::item:alternate,
    QListView::item:alternate,
    QListWidget::item:alternate {
        background-color: #c79aaa;
        color: #30252c;
    }

    QTableView::item:selected,
    QTreeView::item:selected,
    QListView::item:selected,
    QListWidget::item:selected {
        background-color: #9e3f68;
        color: #ffffff;
    }

    QTableView::item:hover,
    QTreeView::item:hover,
    QListView::item:hover,
    QListWidget::item:hover {
        background-color: #dc91ad;
        color: #30252c;
    }

    QTableView::indicator,
    QTreeView::indicator,
    QListView::indicator {
        background-color: #e1b8c9;
        border: 1px solid #bf8fa3;
    }

    QTableView::indicator:checked,
    QTreeView::indicator:checked,
    QListView::indicator:checked {
        background-color: #c94f82;
        border: 1px solid #ae416b;
    }

    /* ==================================================
       TABS
       ================================================== */
    QTabWidget {
        background-color: #e7bfd0;
    }

    QTabWidget::pane {
        background-color: #e7bfd0;
        border: 1px solid #c59bab;
    }

    QTabBar {
        background-color: #d5a5b9;
    }

    QTabBar::tab {
        background-color: #d5a5b9;
        color: #5c4650;
        padding: 6px 12px;
        border: 1px solid #c59bab;
    }

    QTabBar::tab:selected {
        background-color: #9e3f68;
        color: #ffffff;
        border: 1px solid #ae416b;
    }

    QTabBar::tab:hover {
        background-color: #dc91ad;
        color: #30252c;
    }

    /* ==================================================
       MENUS
       ================================================== */
    QMenu {
        background-color: #e7bfd0;
        color: #30252c;
        border: 1px solid #c79aaa;
    }

    QMenu::item {
        background-color: transparent;
        color: #30252c;
        padding: 5px 12px;
    }

    QMenu::item:selected {
        background-color: #9e3f68;
        color: #ffffff;
    }

    /* ==================================================
       CHECKBOXES / RADIO BUTTONS
       ================================================== */
    QCheckBox,
    QRadioButton {
        background-color: transparent;
        color: #30252c;
    }

    QCheckBox::indicator,
    QRadioButton::indicator {
        width: 14px;
        height: 14px;
        background-color: #f0d7e1;
        border: 1px solid #c79aaa;
        border-radius: 2px;
    }

    QCheckBox::indicator:checked,
    QRadioButton::indicator:checked {
        background-color: #ae416b;
        border: 1px solid #93365b;
    }

    QCheckBox::indicator:unchecked:hover,
    QRadioButton::indicator:unchecked:hover {
        background-color: #dc91ad;
        border: 1px solid #ae416b;
    }

    /* ==================================================
       PASSWORD DIALOG
       ================================================== */
    PasswordLineEdit {
        background-color: #f0d7e1;
        color: #30252c;
    }

    PasswordLineEdit QLineEdit,
    ButtonsLineEdit {
        background-color: #f6e5ec;
        color: #30252c;
        border: 1px solid #c79aaa;
        selection-background-color: #9e3f68;
        selection-color: #ffffff;
    }

    PasswordLineEdit QPushButton,
    ButtonsLineEdit QPushButton {
        background-color: #f0d7e1;
        color: #30252c;
        border: 1px solid #c79aaa;
    }

    /* ==================================================
       FORM SECTIONS
       ================================================== */
    #FormFrame {
        background-color: #dcb0c3;
        border: 1px solid #c59bab;
    }

    #FormSectionLabel {
        background-color: transparent;
        color: #6b505b;
    }

    #FormSectionTitle {
        background-color: transparent;
        color: #30252c;
    }

    #FormSeparatorLine {
        background-color: transparent;
        border: 1px solid #c59bab;
    }

    /* ==================================================
       TOOLTIPS
       ================================================== */
    QToolTip {
        background-color: #f0d7e1;
        color: #30252c;
        border: 1px solid #c79aaa;
    }

    /* ==================================================
       SCROLLBARS
       ================================================== */
    QScrollBar:vertical {
        background-color: #d5a5b9;
        width: 12px;
        margin: 0px;
    }

    QScrollBar::handle:vertical {
        background-color: #c184a0;
        min-height: 30px;
        border-radius: 5px;
    }

    QScrollBar::handle:vertical:hover {
        background-color: #ae416b;
    }

    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical {
        background: none;
        border: none;
    }

    QScrollBar:horizontal {
        background-color: #d5a5b9;
        height: 12px;
        margin: 0px;
    }

    QScrollBar::handle:horizontal {
        background-color: #c184a0;
        min-width: 30px;
        border-radius: 5px;
    }

    QScrollBar::handle:horizontal:hover {
        background-color: #ae416b;
    }

    QScrollBar::add-line:horizontal,
    QScrollBar::sub-line:horizontal {
        background: none;
        border: none;
    }
"""

ORANGE_THEME = """
    /* ==================================================
       BASE / WINDOWS
       ================================================== */
    QWidget,
    QMainWindow,
    QDialog,
    QFrame,
    QGroupBox {
        background-color: #e8a84e;
        color: #3a2a1a;
    }

    QLabel {
        background-color: transparent;
        color: #3a2a1a;
    }

    /* ==================================================
       INPUTS
       ================================================== */
    QLineEdit,
    QTextEdit,
    QPlainTextEdit,
    QSpinBox,
    QDoubleSpinBox,
    QComboBox {
        background-color: #fff0d2;
        color: #3a2a1a;
        border: 1px solid #c98224;
        border-radius: 3px;
        padding: 3px;
        selection-background-color: #a65300;
        selection-color: #ffffff;
    }

    QLineEdit:focus,
    QTextEdit:focus,
    QPlainTextEdit:focus,
    QSpinBox:focus,
    QDoubleSpinBox:focus,
    QComboBox:focus {
        border: 1px solid #c96b00;
    }

    QLineEdit:disabled,
    QTextEdit:disabled,
    QPlainTextEdit:disabled,
    QSpinBox:disabled,
    QDoubleSpinBox:disabled,
    QComboBox:disabled {
        background-color: #d9b47b;
        color: #80684c;
    }

    /* ==================================================
       COMBO BOX POPUPS
       ================================================== */
    QComboBox QAbstractItemView {
        background-color: #fff0d2;
        color: #3a2a1a;
        border: 1px solid #c98224;
        selection-background-color: #a65300;
        selection-color: #ffffff;
        outline: none;
    }

    QComboBox QAbstractItemView::item {
        background-color: #fff0d2;
        color: #3a2a1a;
        padding: 4px;
    }

    QComboBox QAbstractItemView::item:selected {
        background-color: #a65300;
        color: #ffffff;
    }

    /* ==================================================
       TOOLBARS / ACCOUNT BAR
       ================================================== */
    QToolBar {
        background-color: #e8a84e;
        border: none;
        spacing: 0px;
    }

    QToolBar::separator {
        background-color: #e8a84e;
        width: 0px;
        height: 0px;
    }

    QToolBar QToolButton {
        background-color: transparent;
        color: #3a2a1a;
        border: none;
        margin: 0px;
        padding: 0px;
    }

    QToolBar QToolButton:hover {
        background-color: #f2bd68;
        color: #3a2a1a;
    }

    QToolBar QToolButton:pressed,
    QToolBar QToolButton:checked {
        background-color: #a65300;
        color: #ffffff;
    }

    /* ==================================================
       BUTTONS
       ================================================== */
    QPushButton {
        background-color: #fff0d2;
        color: #3a2a1a;
        border: 1px solid #c98224;
        border-radius: 3px;
        padding: 5px 10px;
    }

    QPushButton:hover {
        background-color: #f2bd68;
        border: 1px solid #c96b00;
    }

    QPushButton:pressed {
        background-color: #a65300;
        color: #ffffff;
    }

    QPushButton:disabled {
        background-color: #d9b47b;
        color: #80684c;
        border-color: #bf955e;
    }

    /* ==================================================
       TABLES / TREES / LISTS
       ================================================== */
    QTableView,
    QTreeView,
    QListView,
    QListWidget {
        background-color: #df9f43;
        color: #3a2a1a;
        alternate-background-color: #d49336;
        gridline-color: #bd7b22;
        border: 1px solid #c98224;
        selection-background-color: #a65300;
        selection-color: #ffffff;
    }

    QTableView::item,
    QListView::item,
    QListWidget::item {
        background: none;
        color: #3a2a1a;
    }

    QTreeView::item {
        background: none;
        color: #3a2a1a;
        padding: 0px 0px 0px 4px;
    }

    QTableView::item:alternate,
    QTreeView::item:alternate,
    QListView::item:alternate,
    QListWidget::item:alternate {
        background-color: #d49336;
        color: #3a2a1a;
    }

    QTableView::item:selected,
    QTreeView::item:selected,
    QListView::item:selected,
    QListWidget::item:selected {
        background-color: #a65300;
        color: #ffffff;
    }

    QTableView::item:hover,
    QTreeView::item:hover,
    QListView::item:hover,
    QListWidget::item:hover {
        background-color: #f2bd68;
        color: #3a2a1a;
    }

    QTableView::indicator,
    QTreeView::indicator,
    QListView::indicator {
        background-color: #e8b96b;
        border: 1px solid #bd7b22;
    }

    QTableView::indicator:checked,
    QTreeView::indicator:checked,
    QListView::indicator:checked {
        background-color: #a65300;
        border: 1px solid #c96b00;
    }

    /* ==================================================
       TABS
       ================================================== */
    QTabWidget {
        background-color: #e8a84e;
    }

    QTabWidget::pane {
        background-color: #e8a84e;
        border: 1px solid #c98224;
    }

    QTabBar {
        background-color: #d9973f;
    }

    QTabBar::tab {
        background-color: #d9973f;
        color: #5a3b1e;
        padding: 6px 12px;
        border: 1px solid #c98224;
    }

    QTabBar::tab:selected {
        background-color: #a65300;
        color: #ffffff;
        border: 1px solid #c96b00;
    }

    QTabBar::tab:hover {
        background-color: #f2bd68;
        color: #3a2a1a;
    }

    /* ==================================================
       MENUS
       ================================================== */
    QMenu {
        background-color: #e8a84e;
        color: #3a2a1a;
        border: 1px solid #c98224;
    }

    QMenu::item {
        background-color: transparent;
        color: #3a2a1a;
        padding: 5px 12px;
    }

    QMenu::item:selected {
        background-color: #a65300;
        color: #ffffff;
    }

    /* ==================================================
       CHECKBOXES / RADIO BUTTONS
       ================================================== */
    QCheckBox,
    QRadioButton {
        background-color: transparent;
        color: #3a2a1a;
    }

    QCheckBox::indicator,
    QRadioButton::indicator {
        width: 14px;
        height: 14px;
        background-color: #fff0d2;
        border: 1px solid #c98224;
        border-radius: 2px;
    }

    QCheckBox::indicator:checked,
    QRadioButton::indicator:checked {
        background-color: #a65300;
        border: 1px solid #c96b00;
    }

    QCheckBox::indicator:unchecked:hover,
    QRadioButton::indicator:unchecked:hover {
        background-color: #f2bd68;
        border: 1px solid #c96b00;
    }

    /* ==================================================
       PASSWORD DIALOG
       ================================================== */
    PasswordLineEdit {
        background-color: #fff0d2;
        color: #3a2a1a;
    }

    PasswordLineEdit QLineEdit,
    ButtonsLineEdit {
        background-color: #fff7e8;
        color: #3a2a1a;
        border: 1px solid #c98224;
        selection-background-color: #a65300;
        selection-color: #ffffff;
    }

    PasswordLineEdit QPushButton,
    ButtonsLineEdit QPushButton {
        background-color: #fff0d2;
        color: #3a2a1a;
        border: 1px solid #c98224;
    }

    /* ==================================================
       FORM SECTIONS
       ================================================== */
    #FormFrame {
        background-color: #df9f43;
        border: 1px solid #c98224;
    }

    #FormSectionLabel {
        background-color: transparent;
        color: #704719;
    }

    #FormSectionTitle {
        background-color: transparent;
        color: #3a2a1a;
    }

    #FormSeparatorLine {
        background-color: transparent;
        border: 1px solid #c98224;
    }

    /* ==================================================
       TOOLTIPS
       ================================================== */
    QToolTip {
        background-color: #fff0d2;
        color: #3a2a1a;
        border: 1px solid #c98224;
    }

    /* ==================================================
       SCROLLBARS
       ================================================== */
    QScrollBar:vertical {
        background-color: #d9973f;
        width: 12px;
        margin: 0px;
    }

    QScrollBar::handle:vertical {
        background-color: #c98224;
        min-height: 30px;
        border-radius: 5px;
    }

    QScrollBar::handle:vertical:hover {
        background-color: #c96b00;
    }

    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical {
        background: none;
        border: none;
    }

    QScrollBar:horizontal {
        background-color: #d9973f;
        height: 12px;
        margin: 0px;
    }

    QScrollBar::handle:horizontal {
        background-color: #c98224;
        min-width: 30px;
        border-radius: 5px;
    }

    QScrollBar::handle:horizontal:hover {
        background-color: #c96b00;
    }

    QScrollBar::add-line:horizontal,
    QScrollBar::sub-line:horizontal {
        background: none;
        border: none;
    }
"""


LIGHT_FORM_STYLE = """
#FormSeparatorLine {
    border: 1px solid #E3E2E2;
}

#FormSectionLabel {
    background-color: transparent;
    color: #444444;
}

#FormSectionTitle {
    background-color: transparent;
    color: #444444;
}

#FormFrame {
    background-color: #F2F2F2;
    border: 1px solid #E3E2E2;
}
"""


DARK_FORM_STYLE = """
#FormSeparatorLine {
    border: 1px solid #454950;
    background-color: transparent;
}

#FormSectionLabel {
    background-color: transparent;
    color: #e2e4e7;
}

#FormSectionTitle {
    background-color: transparent;
    color: #e2e4e7;
}

#FormFrame {
    background-color: #303236;
    border: 1px solid #454950;
}
"""

PINK_FORM_STYLE = """
#FormSeparatorLine {
    border: 1px solid #d5b5c3;
    background-color: transparent;
}

#FormSectionLabel {
    background-color: transparent;
    color: #6b505b;
}

#FormSectionTitle {
    background-color: transparent;
    color: #30252c;
}

#FormFrame {
    background-color: #ead1dc;
    border: 1px solid #d5b5c3;
}
"""

ORANGE_FORM_STYLE = """
#FormSeparatorLine {
    border: 1px solid #d6a15c;
    background-color: transparent;
}

#FormSectionLabel {
    background-color: transparent;
    color: #765b3c;
}

#FormSectionTitle {
    background-color: transparent;
    color: #3a2a1a;
}

#FormFrame {
    background-color: #ead1ad;
    border: 1px solid #d6a15c;
}
"""

LIGHT_ACCOUNT_TYPES_STYLE = """
QListWidget {
    background-color: white;
    color: black;
    border: 1px solid #E3E2E2;
}

QListWidget::item {
    background-color: white;
    color: black;
}

QListWidget::item:selected {
    background-color: #D3EBFF;
    color: black;
}

QListWidget::item:hover {
    background-color: #F5F8FA;
    color: black;
}
"""


DARK_ACCOUNT_TYPES_STYLE = """
QListWidget {
    background-color: #303236;
    color: #f1f2f3;
    border: 1px solid #454950;
}

QListWidget::item {
    background-color: #303236;
    color: #f1f2f3;
    padding: 4px;
}

QListWidget::item:selected {
    background-color: #526b88;
    color: #ffffff;
}

QListWidget::item:hover {
    background-color: #41454b;
    color: #ffffff;
}
"""


PINK_ACCOUNT_TYPES_STYLE = """
QListWidget {
    background-color: #eed6e1;
    color: #30252c;
    border: 1px solid #cdaabb;
}

QListWidget::item {
    background-color: #eed6e1;
    color: #30252c;
    padding: 4px;
}

QListWidget::item:selected {
    background-color: #9e3f68;
    color: #ffffff;
}

QListWidget::item:hover {
    background-color: #edbfd2;
    color: #30252c;
}
"""

ORANGE_ACCOUNT_TYPES_STYLE = """
QListWidget {
    background-color: #ffffff;
    color: #3a2a1a;
    border: 1px solid #d6a15c;
}

QListWidget::item {
    background-color: #ffffff;
    color: #3a2a1a;
    padding: 4px;
}

QListWidget::item:selected {
    background-color: #a65300;
    color: #ffffff;
}

QListWidget::item:hover {
    background-color: #f5b45c;
    color: #3a2a1a;
}
"""


LIGHT_WALLET_WINDOW_STYLE = """
QListView {
    alternate-background-color: #F5F8FA;
}

QListView::item {
    color: black;
}

QListView::item:selected {
    background-color: #D3EBFF;
    color: black;
}

QTableView {
    outline: 0;
    alternate-background-color: #F5F8FA;
}

QTableView::item {
    color: black;
    border: 0px;
}

QTableView::item:selected {
    background-color: #D3EBFF;
    color: black;
}

QTableView::item:focus {
    color: black;
    background-color: #D3EBFF;
    border: 0px;
}

QTreeView {
    alternate-background-color: #F5F8FA;
}

QTreeView::item {
    color: black;
    padding: 0px 0px 0px 4px;
}

#NotificationCard {
    background-color: white;
    color: black;
    border-bottom: 1px solid #E3E2E2;
}

#NotificationCardImage {
    background-color: white;
    padding: 4px;
    border: 1px solid #E2E2E2;
}

#NotificationCardTitle {
    background-color: transparent;
    color: black;
    font-weight: bold;
    font-size: 14pt;
}

#NotificationCardDescription {
    background-color: transparent;
    color: black;
}

#NotificationCardContext {
    background-color: transparent;
    color: grey;
}

#FormSeparatorLine {
    border: 1px solid #E3E2E2;
}
"""


DARK_WALLET_WINDOW_STYLE = """
QListView {
    alternate-background-color: #36393e;
    background-color: #303236;
    color: #f1f2f3;
}

QListView::item {
    color: #f1f2f3;
}

QListView::item:selected {
    background-color: #526b88;
    color: #ffffff;
}

QTableView {
    outline: 0;
    alternate-background-color: #36393e;
    background-color: #303236;
    color: #f1f2f3;
}

QTableView::item {
    color: #f1f2f3;
    border: 0px;
}

QTableView::item:selected {
    background-color: #526b88;
    color: #ffffff;
}

QTableView::item:focus {
    color: #f1f2f3;
    background-color: #526b88;
    border: 0px;
}

QTreeView {
    alternate-background-color: #36393e;
    background-color: #303236;
    color: #f1f2f3;
}

QTreeView::item {
    color: #f1f2f3;
    padding: 0px 0px 0px 4px;
}

#NotificationCard {
    background-color: #303236;
    color: #f1f2f3;
    border-bottom: 1px solid #454950;
}

#NotificationCardImage {
    background-color: #303236;
    padding: 4px;
    border: 1px solid #454950;
}

#NotificationCardTitle {
    background-color: transparent;
    color: #f1f2f3;
    font-weight: bold;
    font-size: 14pt;
}

#NotificationCardDescription {
    background-color: transparent;
    color: #f1f2f3;
}

#NotificationCardContext {
    background-color: transparent;
    color: #b8bdc4;
}

#FormSeparatorLine {
    border: 1px solid #454950;
}
"""


PINK_WALLET_WINDOW_STYLE = """
QListView {
    alternate-background-color: #e7cbd7;
    background-color: #eed6e1;
    color: #30252c;
}

QListView::item {
    color: #30252c;
}

QListView::item:selected {
    background-color: #9e3f68;
    color: #ffffff;
}

QTableView {
    outline: 0;
    alternate-background-color: #e7cbd7;
    background-color: #eed6e1;
    color: #30252c;
}

QTableView::item {
    color: #30252c;
    border: 0px;
}

QTableView::item:selected {
    background-color: #9e3f68;
    color: #ffffff;
}

QTableView::item:focus {
    color: #ffffff;
    background-color: #9e3f68;
    border: 0px;
}

QTreeView {
    alternate-background-color: #e7cbd7;
    background-color: #eed6e1;
    color: #30252c;
}

QTreeView::item {
    color: #30252c;
    padding: 0px 0px 0px 4px;
}

QTreeView::item:selected {
    background-color: #9e3f68;
    color: #ffffff;
}

#NotificationCard {
    background-color: #ead1dc;
    color: #30252c;
    border-bottom: 1px solid #d5b5c3;
}

#NotificationCardImage {
    background-color: #ead1dc;
    padding: 4px;
    border: 1px solid #d5b5c3;
}

#NotificationCardTitle {
    background-color: transparent;
    color: #30252c;
    font-weight: bold;
    font-size: 14pt;
}

#NotificationCardDescription {
    background-color: transparent;
    color: #30252c;
}

#NotificationCardContext {
    background-color: transparent;
    color: #765d68;
}

#FormSeparatorLine {
    border: 1px solid #d5b5c3;
}
"""

ORANGE_WALLET_WINDOW_STYLE = """
QListView {
    alternate-background-color: #e7c9a5;
    background-color: #ffffff;
    color: #3a2a1a;
}

QListView::item {
    color: #3a2a1a;
}

QListView::item:selected {
    background-color: #a65300;
    color: #ffffff;
}

QTableView {
    outline: 0;
    alternate-background-color: #e7c9a5;
    background-color: #ffffff;
    color: #3a2a1a;
}

QTableView::item {
    color: #3a2a1a;
    border: 0px;
}

QTableView::item:selected {
    background-color: #a65300;
    color: #ffffff;
}

QTableView::item:focus {
    color: #ffffff;
    background-color: #a65300;
    border: 0px;
}

QTreeView {
    alternate-background-color: #e7c9a5;
    background-color: #ffffff;
    color: #3a2a1a;
}

QTreeView::item {
    color: #3a2a1a;
    padding: 0px 0px 0px 4px;
}

QTreeView::item:selected {
    background-color: #a65300;
    color: #ffffff;
}

#NotificationCard {
    background-color: #ead1ad;
    color: #3a2a1a;
    border-bottom: 1px solid #d6a15c;
}

#NotificationCardImage {
    background-color: #ead1ad;
    padding: 4px;
    border: 1px solid #d6a15c;
}

#NotificationCardTitle {
    background-color: transparent;
    color: #3a2a1a;
    font-weight: bold;
    font-size: 14pt;
}

#NotificationCardDescription {
    background-color: transparent;
    color: #3a2a1a;
}

#NotificationCardContext {
    background-color: transparent;
    color: #765b3c;
}

#FormSeparatorLine {
    border: 1px solid #d6a15c;
}
"""


def theme_accent(theme: Theme) -> str:
    # These deeper shades keep white labels/checkmarks readable on every accent.
    return {Theme.PINK: '#9e3f68', Theme.ORANGE: '#a65300'}.get(theme, '#0877d1')


def theme_palette(theme: Theme) -> QPalette:
    # Start fresh on every switch. Reusing app.palette() retains the previous
    # native dark palette in controls that a stylesheet does not cover.
    colors = {
        Theme.LIGHT: ('#f2f2f2', '#ffffff', '#f5f8fa', '#222222', '#d3ebff', '#000000'),
        Theme.DARK: ('#202124', '#303236', '#383b40', '#e8eaed', '#526b88', '#ffffff'),
        Theme.PINK: ('#e7bfd0', '#eed5df', '#d4aabd', '#30252c', '#9e3f68', '#ffffff'),
        Theme.ORANGE: ('#ead1ad', '#ffffff', '#e7c9a5', '#3a2a1a', '#a65300', '#ffffff'),
    }
    window, base, alternate, text, highlight, highlighted = colors[theme]
    palette = QPalette(QColor(window))
    for role, color in ((QPalette.Window, window), (QPalette.Base, base),
            (QPalette.AlternateBase, alternate), (QPalette.WindowText, text),
            (QPalette.Text, text), (QPalette.Button, base), (QPalette.ButtonText, text),
            (QPalette.ToolTipBase, base), (QPalette.ToolTipText, text),
            (QPalette.Highlight, highlight), (QPalette.HighlightedText, highlighted),
            (QPalette.BrightText, '#ff8080' if theme == Theme.DARK else '#b42318'),
            (QPalette.PlaceholderText, '#aeb2b8' if theme == Theme.DARK else '#62666d'),
            (QPalette.Link, '#70b7ff' if theme == Theme.DARK else '#005bbb'),
            (QPalette.LinkVisited, '#b8a0eb' if theme == Theme.DARK else '#7040a0')):
        palette.setColor(role, QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role,
            QColor('#92969c' if theme == Theme.DARK else '#777777'))
    return palette


def apply_theme(app, theme: Theme) -> None:
    if theme == Theme.DARK:
        stylesheet = DARK_THEME
    elif theme == Theme.PINK:
        stylesheet = PINK_THEME
    elif theme == Theme.ORANGE:
        stylesheet = ORANGE_THEME
    else:
        stylesheet = LIGHT_THEME

    app.setStyleSheet('')
    app.setPalette(theme_palette(theme))
    from .theme_controls import control_style
    app.setStyleSheet(stylesheet + control_style(theme == Theme.DARK, theme_accent(theme)))

    dark = theme == Theme.DARK


    # Remember the current theme for newly-created windows.
    app._dark_theme = dark

    for widget in app.allWidgets():
        if widget.property('theme_role') == 'form':
            widget.update_theme(theme)
        elif widget.property('theme_role') == 'account_types':
            widget.setStyleSheet(account_types_style(theme))

    for window in app.topLevelWidgets():
        if isinstance(window, (QMainWindow, QDialog)):
            apply_windows_title_bar(window, theme)


def form_style(theme: Theme) -> str:
    if theme == Theme.DARK:
        return DARK_FORM_STYLE
    elif theme == Theme.PINK:
        return PINK_FORM_STYLE
    elif theme == Theme.ORANGE:
        return ORANGE_FORM_STYLE
    else:
        return LIGHT_FORM_STYLE


def account_types_style(theme: Theme) -> str:
    if theme == Theme.DARK:
        return DARK_ACCOUNT_TYPES_STYLE
    elif theme == Theme.PINK:
        return PINK_ACCOUNT_TYPES_STYLE
    elif theme == Theme.ORANGE:
        return ORANGE_ACCOUNT_TYPES_STYLE
    else:
        return LIGHT_ACCOUNT_TYPES_STYLE

def wallet_window_style(theme: Theme) -> str:
    if theme == Theme.DARK:
        return DARK_WALLET_WINDOW_STYLE
    elif theme == Theme.PINK:
        return PINK_WALLET_WINDOW_STYLE
    elif theme == Theme.ORANGE:
        return ORANGE_WALLET_WINDOW_STYLE
    else:
        return LIGHT_WALLET_WINDOW_STYLE


def apply_windows_title_bar(window, theme: Theme) -> None:
    if sys.platform != "win32":
        return

    if not isinstance(window, (QMainWindow, QDialog)):
        return

    try:
        import ctypes

        hwnd = int(window.winId())
        build = sys.getwindowsversion().build

        # DWMWA_USE_IMMERSIVE_DARK_MODE
        dark_attribute = 20 if build >= 17763 else 19

        dark = theme == Theme.DARK
        dark_value = ctypes.c_int(1 if dark else 0)

        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd),
            dark_attribute,
            ctypes.byref(dark_value),
            ctypes.sizeof(dark_value),
        )

    except Exception:
        pass




def debug_widget_at_cursor() -> None:
    app = QApplication.instance()
    if app is None:
        return

    pos = QCursor.pos()

    for window in app.topLevelWidgets():
        if not window.isVisible():
            continue

        print(
            "WINDOW:",
            window.metaObject().className(),
            "geometry=", window.geometry().getRect(),
            "frameGeometry=", window.frameGeometry().getRect(),
            "contentsRect=", window.contentsRect().getRect(),
        )

        local_pos = window.mapFromGlobal(pos)

        if not window.rect().contains(local_pos):
            continue

        child = window.childAt(local_pos)

        if child is None:
            child = window

        palette = child.palette()

        print(
            "WIDGET UNDER CURSOR:",
            child.metaObject().className(),
            "objectName=", repr(child.objectName()),
            "parent=",
            (
                child.parentWidget().metaObject().className()
                if child.parentWidget() else None
            ),
            "geometry=", child.geometry().getRect(),
            "stylesheet=", repr(child.styleSheet()),
            "autoFill=", child.autoFillBackground(),
            "windowColor=", palette.color(QPalette.Window).name(),
            "baseColor=", palette.color(QPalette.Base).name(),
        )

        print(
            "WIDGET UNDER CURSOR:",
            child.metaObject().className(),
            "objectName=", repr(child.objectName()),
            "parent=",
            (
                child.parentWidget().metaObject().className()
                if child.parentWidget() else None
            ),
            "geometry=", child.geometry().getRect(),
            "stylesheet=", repr(child.styleSheet()),
            "autoFill=", child.autoFillBackground(),
            "windowColor=", palette.color(QPalette.Window).name(),
            "baseColor=", palette.color(QPalette.Base).name(),
        )

