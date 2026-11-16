"""Palette-aware device status glyphs, independent of the application's theme layer.

USB artwork: Lucide 86d323875837015ddf74d044df1f4c09e99c6885.
The ISC/MIT notices are bundled with data/icons/lucide/usb.svg.
"""
from PyQt5.QtCore import QRectF, Qt
from PyQt5.QtGui import QColor, QIcon, QIconEngine, QPainter, QPalette, QPen, QPixmap
from PyQt5.QtWidgets import QApplication
from electrumsv.util import resource_path


class PaletteIconEngine(QIconEngine):
    def __init__(self, path):
        super().__init__()
        self.path = path
        self.source = QIcon(path)

    def clone(self):
        return PaletteIconEngine(self.path)

    def pixmap(self, size, mode, state):
        palette = QApplication.palette()
        if mode == QIcon.Disabled:
            color = palette.color(QPalette.Disabled, QPalette.WindowText)
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


def hardware_icon(state):
    badge, color = {'none': ('cross', '#c83232'),
        'unavailable': ('!', '#956000'), 'available': ('check', '#20864e')}[state]
    return QIcon(BadgedIconEngine(resource_path('icons', 'lucide', 'usb.svg'), badge, color))
