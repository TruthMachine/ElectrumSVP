"""Theme-aware rendering for explicitly identified single-color UI glyphs."""
from PyQt5.QtCore import QRectF, Qt
from PyQt5.QtGui import QColor, QIcon, QIconEngine, QPainter, QPalette, QPen, QPixmap
from PyQt5.QtWidgets import QApplication, QLabel


# Public icon names stay stable for callers. Only UI glyphs are replaced;
# vendor logos and transaction illustrations retain their original artwork.
LUCIDE_ICONS = {
    'tab_history.png': 'rotate-ccw-clock', 'icons8-transaction-list-96.png': 'list',
    'tab_send.png': 'send', 'tab_receive.png': 'arrow-down-left',
    'tab_keys.png': 'contact-round', 'tab_contacts.png': 'contact-round',
    'icons8-event-64-cute-clipart.png': 'bell', 'tab_coins.png': 'coins',
    'tab_console.png': 'terminal', 'icons8-add-folder-80.png': 'folder-plus',
    'icons8-moleskine-80.png': 'logs', 'network.png': 'network',
    'preferences.png': 'settings', 'icons8-available-updates-80-blue': 'refresh-cw',
    'refresh_win10_16.png': 'refresh-cw', 'icons8-filter-edit-32-windows.png': 'funnel',
    'icons8-clear-filters-32-windows.png': 'funnel-x',
    'icons8-export-32-windows.png': 'download', 'sb_balance.png': 'coins',
    'sb_fiat.png': 'banknote', 'sb_network.png': 'globe',
    'sb_approximate.png': 'equal-approximately', 'qrcode.png': 'qr-code',
    'qrcode_white.png': 'qr-code', 'file.png': 'file',
    'icons8-copy-to-clipboard-32.png': 'copy',
    'icons8-question-mark-96.png': 'circle-question-mark',
}
NAVIGATION_ICONS = frozenset(name for name in LUCIDE_ICONS if name.startswith('tab_')) | {
    'icons8-transaction-list-96.png', 'icons8-event-64-cute-clipart.png'}


MONOCHROME_ICONS = frozenset({
    'refresh_win10_16.png',
    'icons8-filter-edit-32-windows.png',
    'icons8-clear-filters-32-windows.png',
    'icons8-export-32-windows.png',
    'qrcode.png', 'qrcode_white.png', 'sb_balance.png', 'sb_fiat.png', 'sb_network.png', 'sb_approximate.png',
})

NOTIFICATION_ICONS = frozenset({'icons8-topic-32.png',
    'icons8-topic-32-windows-urgent.png', 'icons8-topic-32-windows-plus.png',
    *('icons8-topic-32-windows-{}.png'.format(i) for i in range(1, 6))})


class PaletteIconEngine(QIconEngine):
    def __init__(self, path, status_hint=False, selected_accent=False):
        super().__init__()
        self.path = path
        self.status_hint = status_hint
        self.selected_accent = selected_accent
        self.source = QIcon(path)

    def clone(self):
        return PaletteIconEngine(self.path, self.status_hint, self.selected_accent)

    def pixmap(self, size, mode, state):
        palette = QApplication.palette()
        if mode == QIcon.Disabled:
            color = palette.color(QPalette.Disabled, QPalette.WindowText)
        elif self.selected_accent and state == QIcon.On:
            color = QColor('#ffffff')
        elif mode == QIcon.Selected:
            color = palette.color(QPalette.HighlightedText)
        elif self.status_hint:
            dark = palette.color(QPalette.Window).lightness() < 128
            color = QColor('#d6a2ef' if dark else '#7a32a8')
        else:
            color = palette.color(QPalette.WindowText)
        # Recolor only this explicitly monochrome glyph's alpha mask. Colored
        # account, network and other toolbar artwork never enters this engine.
        if self.path.endswith('.svg'):
            from PyQt5.QtSvg import QSvgRenderer
            result = QPixmap(size)
            result.fill(Qt.transparent)
            vector_painter = QPainter(result)
            QSvgRenderer(self.path).render(vector_painter, QRectF(result.rect()))
            vector_painter.end()
        else:
            result = self.source.pixmap(size, QIcon.Normal, state).scaled(size)
            result.setDevicePixelRatio(1)
        painter = QPainter(result)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(result.rect(), color)
        painter.end()
        return result

    def paint(self, painter, rect, mode, state):
        ratio = painter.device().devicePixelRatioF()
        pixmap = self.pixmap(rect.size() * ratio, mode, state)
        pixmap.setDevicePixelRatio(ratio)
        painter.drawPixmap(rect, pixmap)

    def scaledPixmap(self, size, mode, state, scale):
        pixmap = self.pixmap(size * scale, mode, state)
        pixmap.setDevicePixelRatio(scale)
        return pixmap


class PaletteIconLabel(QLabel):
    """A status-bar glyph that is repainted instead of retaining a stale pixmap."""
    def __init__(self, path):
        super().__init__()
        self.icon = QIcon(PaletteIconEngine(path))
        self.setFixedSize(16, 16)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        self.icon.paint(painter, self.rect(), Qt.AlignCenter,
            QIcon.Normal if self.isEnabled() else QIcon.Disabled)


class BadgedIconEngine(PaletteIconEngine):
    """A scalable glyph with a semantic badge, independent of the theme tint."""
    def __init__(self, path, badge, color='#c83232'):
        super().__init__(path)
        self.badge, self.color = badge, color

    def clone(self):
        return BadgedIconEngine(self.path, self.badge, self.color)

    def pixmap(self, size, mode, state):
        result = super().pixmap(size, mode, state)
        if not self.badge:
            return result
        painter = QPainter(result)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.scale(result.width() / 24, result.height() / 24)
        painter.setPen(QPen(QApplication.palette().color(QPalette.Window), 1))
        painter.setBrush(QColor(self.color))
        painter.drawEllipse(QRectF(12.5, 12.5, 11, 11))
        painter.setPen(QPen(QColor('white'), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        if self.badge == 'check':
            from PyQt5.QtCore import QPointF
            painter.drawPolyline(QPointF(15.5, 18), QPointF(17.3, 20), QPointF(21, 16))
        elif self.badge == 'cross':
            painter.drawLine(16, 16, 20, 20)
            painter.drawLine(20, 16, 16, 20)
        else:
            font = QApplication.font()
            font.setPixelSize(8)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(QRectF(13, 12.5, 10, 11), Qt.AlignCenter, self.badge)
        painter.end()
        return result
