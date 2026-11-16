"""Shared control details; leave page structure and platform fonts unchanged."""
from electrumsv.util import resource_path
from PyQt5.QtCore import QRectF
from PyQt5.QtGui import QColor, QPainter, QPalette, QPen
from PyQt5.QtWidgets import QTabBar, QTabWidget


class SegmentedTabBar(QTabBar):
    def paintEvent(self, event):
        super().paintEvent(event)
        # QTabBar's native document baseline crosses/clips the stylesheet's
        # rounded frame. Draw one closed outline entirely inside the widget.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        dark = self.palette().color(QPalette.Window).lightness() < 128
        painter.setPen(QPen(QColor('#454950' if dark else '#d5d8dc'), 1))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 6, 6)


class SegmentedTabWidget(QTabWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTabBar(SegmentedTabBar())
        self.setDocumentMode(True)
        self.tabBar().setDrawBase(False)
        self.tabBar().setExpanding(True)
        self.setUsesScrollButtons(True)


def control_style(dark, accent='#0877d1'):
    foreground = '#e8eaed' if dark else '#222222'
    border = '#777d85' if dark else '#858b93'
    surface = '#303236' if dark else '#f2f2f2'
    hover = '#454950' if dark else '#e1e5e9'
    selected = accent
    separator = '#454950' if dark else '#d5d8dc'
    disabled = '#969ba3' if dark else '#71767c'
    icons = resource_path('icons').replace('\\', '/')
    variant = 'dark' if dark else 'light'
    return f'''
    QCheckBox, QRadioButton {{ spacing: 7px; min-height: 22px; background: transparent; }}
    QCheckBox::indicator {{ width: 14px; height: 14px; border-radius: 3px;
        border: 1px solid {border}; background: palette(base); }}
    QCheckBox::indicator:checked {{ background: {selected}; border-color: {selected};
        image: url("{icons}/control-check.svg"); }}
    QCheckBox::indicator:indeterminate {{ background: {selected}; border-color: {selected};
        image: url("{icons}/control-mixed.svg"); }}
    QCheckBox::indicator:disabled {{ border-color: {separator}; background: palette(button); }}
    QCheckBox::indicator:checked:disabled {{ background: #777d85; }}
    QTreeView::indicator:checked, QTableView::indicator:checked {{
        image: url("{icons}/control-check.svg"); background: {selected}; border-color: {selected}; }}
    QCheckBox::indicator:hover, QCheckBox::indicator:focus {{ border-color: {selected}; }}
    QComboBox {{ min-height: 20px; padding: 3px 30px 3px 7px;
        border: 1px solid {border}; border-radius: 5px; background: palette(base); }}
    QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right;
        width: 26px; border: none; background: transparent; }}
    QComboBox::down-arrow {{ image: url("{icons}/control-down-{variant}.svg");
        width: 10px; height: 6px; }}
    QAbstractSpinBox {{ min-height: 20px; padding: 3px 24px 3px 7px;
        border: 1px solid {border}; border-radius: 5px; background: palette(base); }}
    QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-origin: border;
        subcontrol-position: top right; width: 20px; border: none;
        border-left: 1px solid {separator}; background: transparent; }}
    QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-origin: border;
        subcontrol-position: bottom right; width: 20px; border: none;
        border-left: 1px solid {separator}; background: transparent; }}
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
        image: url("{icons}/control-up-{variant}.svg"); width: 8px; height: 5px; }}
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
        image: url("{icons}/control-down-{variant}.svg"); width: 8px; height: 5px; }}
    QSpinBox::up-arrow:off, QSpinBox::up-arrow:disabled,
    QDoubleSpinBox::up-arrow:off, QDoubleSpinBox::up-arrow:disabled {{
        image: url("{icons}/control-up-disabled-{variant}.svg"); }}
    QSpinBox::down-arrow:off, QSpinBox::down-arrow:disabled,
    QDoubleSpinBox::down-arrow:off, QDoubleSpinBox::down-arrow:disabled,
    QComboBox::down-arrow:disabled {{ image: url("{icons}/control-down-disabled-{variant}.svg"); }}
    QSpinBox::up-button:hover, QSpinBox::down-button:hover,
    QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{ background: {hover}; }}
    QLineEdit, QTextEdit, QPlainTextEdit {{ border-radius: 5px;
        border: 1px solid {border}; background: palette(base); color: palette(text);
        padding: 4px 6px; }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
    QComboBox:focus, QAbstractSpinBox:focus {{ border-color: {selected}; }}
    QPushButton {{ border-radius: 5px; border: 1px solid {border};
        background: {surface}; color: {foreground}; padding: 4px 10px; min-height: 20px; }}
    QPushButton:hover {{ background: {hover}; }}
    QPushButton:default {{ background: {selected}; border-color: {selected}; color: #ffffff; }}
    QPushButton:disabled {{ background: {surface}; color: {disabled};
        border-color: {separator}; }}
    QTabWidget::pane {{ border: none; background: {surface}; }}
    QTabWidget::tab-bar {{ alignment: center; }}
    QTabBar {{ background: {surface}; border: none; border-radius: 6px; }}
    QTabBar::tab {{ color: {foreground}; background: transparent;
        border: 1px solid transparent; border-radius: 4px;
        margin: 2px; padding: 4px 10px; min-height: 18px; }}
    QTabBar::tab:selected {{ background: {selected}; color: #ffffff;
        border-color: {selected}; }}
    QTabBar::tab:!selected:hover {{ background: {hover}; }}
    QToolBar#walletToolbar {{ spacing: 6px; padding: 4px 6px; border: none; }}
    QToolBar#walletToolbar QToolButton {{ padding: 5px 8px; min-width: 44px;
        border: 1px solid transparent; border-radius: 5px; }}
    QToolBar#walletToolbar QToolButton:hover {{ background: {hover}; }}
    QToolBar#walletToolbar QToolButton:focus {{ border-color: {selected}; }}
    QTabWidget#walletTabs {{ background: {surface}; }}
    QTabWidget#walletTabs::tab-bar {{ alignment: left; }}
    QTabWidget#walletTabs::pane {{ border-radius: 0; }}
    QTabWidget#walletTabs > QStackedWidget {{ background: {surface}; }}
    #FormFrame {{ border-radius: 6px; }}
    QWidget[theme_role="table_toolbar"] {{ background: {surface}; }}
    QToolButton[theme_role="table_action"] {{ background: transparent;
        border: 1px solid transparent; border-radius: 3px; padding: 3px; }}
    QToolButton[theme_role="table_action"]:hover {{ background: {hover}; }}
    QToolButton[theme_role="table_action"]:focus {{ border-color: {selected}; }}
    QToolButton[theme_role="table_action"]:checked {{ background: {hover}; }}
    QToolButton[theme_role="inline_action"] {{ background: transparent; border: none;
        border-radius: 3px; padding: 3px; }}
    QToolButton[theme_role="inline_action"]:hover {{ background: {hover}; }}
    QToolButton[theme_role="inline_action"]:focus {{ border: 1px solid {selected}; }}
    QWidget[theme_role="options"] {{ border: none; background: transparent; margin: 0; padding: 0; }}
    '''
