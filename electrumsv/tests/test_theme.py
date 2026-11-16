"""Theme switching must be complete and keep System as a preference, not a snapshot."""
import pytest

pytest.importorskip('PyQt5')
from PyQt5.QtGui import QPalette
from PyQt5.QtWidgets import QApplication, QDialog, QLineEdit, QListWidget, QVBoxLayout

from electrumsv.gui.qt import theme as themes


@pytest.fixture
def gui():
    app = QApplication.instance() or QApplication([])
    palette, stylesheet = app.palette(), app.styleSheet()
    yield app
    app.setStyleSheet('')
    app.setPalette(palette)
    app.setStyleSheet(stylesheet)


@pytest.mark.parametrize('start', [themes.Theme.DARK, themes.Theme.PINK, themes.Theme.ORANGE])
def test_light_replaces_previous_palette_for_existing_and_new_controls(gui, start):
    controller = themes.ThemeController(gui, start)
    dialog = QDialog()
    layout = QVBoxLayout(dialog)
    edit = QLineEdit()
    layout.addWidget(edit)
    dialog.show()
    gui.processEvents()
    controller.select(themes.Theme.LIGHT)
    gui.processEvents()
    for widget in (dialog, edit, QLineEdit()):
        widget.ensurePolished()
        assert widget.palette().color(QPalette.Window).lightness() > 200
        assert widget.palette().color(QPalette.Text).lightness() < 80
    assert gui.palette().color(QPalette.Link).name() == '#005bbb'
    dialog.close()


def test_system_changes_only_followed_in_system_mode(gui, monkeypatch):
    current = [themes.Theme.DARK]
    monkeypatch.setattr(themes, 'system_theme', lambda: current[0])
    controller = themes.ThemeController(gui, themes.Theme.SYSTEM)
    changes = []
    controller.changed.connect(changes.append)
    assert controller.effective == themes.Theme.DARK
    current[0] = themes.Theme.LIGHT
    controller.refresh_system_theme()
    assert controller.effective == themes.Theme.LIGHT
    assert controller.preference == themes.Theme.SYSTEM
    assert changes == [themes.Theme.LIGHT]
    controller.select(themes.Theme.DARK)
    controller.refresh_system_theme()
    assert controller.effective == themes.Theme.DARK
    assert not controller._timer.isActive()
    controller.deleteLater()


def test_system_selection_clears_native_override_before_resolving(gui, monkeypatch):
    from electrumsv.gui.qt import macos_appearance
    calls = []
    monkeypatch.setattr(themes, '_uses_appkit', lambda: True)
    monkeypatch.setattr(macos_appearance, 'set_appearance', calls.append)
    def system_theme():
        assert calls[-1] is None
        return themes.Theme.DARK
    monkeypatch.setattr(themes, 'system_theme', system_theme)
    controller = themes.ThemeController(gui, themes.Theme.LIGHT)
    assert calls == [False]
    controller.select(themes.Theme.SYSTEM)
    assert calls == [False, None]
    controller._timer.stop()
    controller.deleteLater()


def test_local_account_styles_follow_theme(gui):
    widget = QListWidget()
    widget.setProperty('theme_role', 'account_types')
    themes.apply_theme(gui, themes.Theme.DARK)
    assert widget.styleSheet() == themes.DARK_ACCOUNT_TYPES_STYLE
    themes.apply_theme(gui, themes.Theme.LIGHT)
    assert widget.styleSheet() == themes.LIGHT_ACCOUNT_TYPES_STYLE


def test_theme_setting_preserves_legacy_and_explicit_choices(monkeypatch):
    assert themes.theme_from_config({'dark_mode': False}) == themes.Theme.LIGHT
    assert themes.theme_from_config({'dark_mode': True}) == themes.Theme.DARK
    assert themes.theme_from_config({'theme': 'light', 'dark_mode': True}) == themes.Theme.LIGHT
    monkeypatch.setattr(themes, 'system_theme', lambda: themes.Theme.DARK)
    assert themes.theme_from_config({'theme': 'system'}) == themes.Theme.DARK
    assert themes.theme_preference_from_config({'theme': 'system'}) == themes.Theme.SYSTEM


def test_switching_to_light_clears_legacy_dark_text_colors(gui):
    from electrumsv.gui.qt.util import ColorScheme
    from PyQt5.QtWidgets import QWidget
    themes.apply_theme(gui, themes.Theme.DARK)
    ColorScheme.update_from_widget(QWidget())
    assert ColorScheme.dark_scheme
    themes.apply_theme(gui, themes.Theme.LIGHT)
    ColorScheme.update_from_widget(QWidget())
    assert not ColorScheme.dark_scheme


def contrast(first, second):
    def luminance(color):
        channels = [value / 255 for value in color.getRgb()[:3]]
        channels = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
            for v in channels]
        return sum(v * weight for v, weight in zip(channels, (0.2126, 0.7152, 0.0722)))
    low, high = sorted((luminance(first), luminance(second)))
    return (high + 0.05) / (low + 0.05)


@pytest.mark.parametrize('theme', [themes.Theme.LIGHT, themes.Theme.DARK])
@pytest.mark.parametrize('foreground,background', [
    (QPalette.WindowText, QPalette.Window), (QPalette.Text, QPalette.Base),
    (QPalette.Text, QPalette.AlternateBase), (QPalette.ButtonText, QPalette.Button),
    (QPalette.ToolTipText, QPalette.ToolTipBase), (QPalette.HighlightedText, QPalette.Highlight),
    (QPalette.Link, QPalette.Base), (QPalette.LinkVisited, QPalette.Base),
    (QPalette.PlaceholderText, QPalette.Base), (QPalette.BrightText, QPalette.Base),
])
def test_light_and_dark_text_palette_has_readable_contrast(theme, foreground, background):
    palette = themes.theme_palette(theme)
    assert contrast(palette.color(foreground), palette.color(background)) >= 4.5


@pytest.mark.parametrize('name', ['refresh_win10_16.png', 'icons8-filter-edit-32-windows.png',
    'icons8-clear-filters-32-windows.png', 'icons8-export-32-windows.png',
    'icons8-question-mark-96.png', 'sb_balance.png', 'sb_fiat.png', 'sb_network.png',
    'sb_approximate.png'])
def test_cached_action_icons_remain_visible_after_theme_switches(gui, name):
    from PyQt5.QtGui import QIcon
    from electrumsv.gui.qt.util import read_QIcon
    icon = read_QIcon(name)
    colors = []
    for theme in [themes.Theme.LIGHT, themes.Theme.DARK, themes.Theme.LIGHT]:
        themes.apply_theme(gui, theme)
        image = icon.pixmap(32, 32, QIcon.Normal).toImage()
        pixels = [image.pixelColor(x, y) for x in range(image.width())
            for y in range(image.height()) if image.pixelColor(x, y).alpha() > 128]
        assert pixels, name
        color = pixels[0]
        colors.append(color.name())
        assert contrast(color, gui.palette().color(QPalette.Window)) >= 4.5
    assert colors[0] == colors[2] and colors[0] != colors[1]


@pytest.mark.parametrize('suffix', ['', '-windows-urgent', '-windows-plus',
    '-windows-1', '-windows-2', '-windows-3', '-windows-4', '-windows-5'])
def test_notification_outline_follows_theme_and_preserves_badge(gui, suffix):
    from electrumsv.gui.qt.util import read_QIcon
    name = 'icons8-topic-32' + suffix + '.png'
    for theme in (themes.Theme.DARK, themes.Theme.LIGHT):
        themes.apply_theme(gui, theme)
        image = read_QIcon(name).pixmap(48, 48).toImage()
        opaque = [image.pixelColor(x, y) for x in range(48) for y in range(48)
            if image.pixelColor(x, y).alpha() > 240]
        foreground = gui.palette().color(QPalette.WindowText)
        assert any(color == foreground for color in opaque)
        assert any(color.red() > 150 and color.green() < 100 for color in opaque) == bool(suffix)


def test_status_bar_uses_live_icons_and_keeps_notification_inside_button(gui):
    from PyQt5.QtWidgets import QMainWindow
    from electrumsv.gui.qt.status_bar import StatusBar
    from electrumsv.gui.qt.theme_icons import PaletteIconLabel
    window = QMainWindow()
    status = StatusBar(window)
    window.setStatusBar(status)
    status.set_balance_status('0 BSV', None)
    status.set_network_status('Offline')
    status.notification_widget.set_notification_state(1)
    window.show()
    for theme in (themes.Theme.DARK, themes.Theme.LIGHT):
        themes.apply_theme(gui, theme)
        gui.processEvents()
        labels = status.findChildren(PaletteIconLabel)
        assert len(labels) == 5
        assert all(label.width() > 0 and label.height() > 0 for label in labels)
        assert not status.grab().isNull()
    window.close()


@pytest.mark.parametrize('theme', [themes.Theme.LIGHT, themes.Theme.DARK])
def test_amount_units_have_their_own_space_at_larger_fonts(gui, theme):
    from electrumsv.gui.qt.amountedit import AmountEdit
    themes.apply_theme(gui, theme)
    edit = AmountEdit(lambda: 'Bitcoin', prefix='≈')
    font = edit.font(); font.setPointSize(18); edit.setFont(font)
    edit.setText('12345678.12345678')
    edit.show(); gui.processEvents(); gui.processEvents()
    margins = edit.textMargins()
    available = edit.width() - margins.left() - margins.right() - 20
    assert available >= edit.fontMetrics().horizontalAdvance(edit.text())
    assert margins.right() > edit.fontMetrics().horizontalAdvance('Bitcoin')
    assert edit.get_amount() == __import__('decimal').Decimal('12345678.12345678')
    edit.close()


def test_navigation_icons_are_scalable_and_selected_icons_are_white(gui):
    from PyQt5.QtCore import QSize
    from PyQt5.QtGui import QIcon
    from electrumsv.gui.qt.theme_icons import NAVIGATION_ICONS, PaletteIconEngine
    from electrumsv.gui.qt.util import icon_path
    for name in NAVIGATION_ICONS:
        engine = PaletteIconEngine(icon_path(name), selected_accent=True)
        pixmap = engine.scaledPixmap(QSize(24, 24), QIcon.Normal, QIcon.On, 2)
        assert pixmap.width() == 48 and pixmap.devicePixelRatioF() == 2
        image = pixmap.toImage()
        opaque = [image.pixelColor(x, y) for x in range(48) for y in range(48)
            if image.pixelColor(x, y).alpha() > 240]
        assert opaque and all(color.name() == '#ffffff' for color in opaque)


@pytest.mark.parametrize('theme', [themes.Theme.PINK, themes.Theme.ORANGE])
def test_colored_theme_controls_use_their_accent_and_readable_labels(gui, theme):
    from PyQt5.QtWidgets import QPushButton, QCheckBox, QVBoxLayout, QWidget
    from PyQt5.QtGui import QColor
    from electrumsv.gui.qt.theme_controls import SegmentedTabWidget
    themes.apply_theme(gui, theme)
    accent = QColor(themes.theme_accent(theme))
    assert contrast(accent, QColor('white')) >= 4.5
    assert gui.palette().color(QPalette.Highlight) == accent
    window = QWidget(); layout = QVBoxLayout(window)
    tabs = SegmentedTabWidget(); tabs.addTab(QWidget(), 'Selected')
    tabs.addTab(QWidget(), 'Other'); layout.addWidget(tabs)
    button = QPushButton('Continue'); button.setDefault(True); layout.addWidget(button)
    check = QCheckBox('Enabled'); check.setChecked(True); layout.addWidget(check)
    window.show(); gui.processEvents()
    # Check rendered controls, not just the stylesheet string: native Qt rules
    # can otherwise override the intended accent.
    for widget in (tabs.tabBar(), button, check):
        image = widget.grab().toImage()
        count = sum(image.pixelColor(x, y).name() == accent.name()
            for x in range(image.width()) for y in range(image.height()))
        assert count > 20, (theme, widget.metaObject().className())
    window.close()
