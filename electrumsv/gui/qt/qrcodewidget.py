import os
from typing import Callable, TYPE_CHECKING

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QCursor, QPainter
from PyQt5.QtWidgets import (
    QApplication, QVBoxLayout, QTextEdit, QHBoxLayout, QPushButton, QWidget)
import qrcode

from electrumsv.i18n import _
from electrumsv.app_state import app_state
from electrumsv.qrtransport import create_frames

from .util import WindowModalDialog

if TYPE_CHECKING:
    from .main_window import ElectrumWindow


class QRCodeWidget(QWidget):

    def __init__(self, data=None, fixedSize=False):
        QWidget.__init__(self)
        self.data = None
        self.qr = None
        self.fixedSize = fixedSize
        if fixedSize:
            self.setFixedSize(fixedSize, fixedSize)
        self.setData(data)

    def clean_up(self) -> None:
        del self.mouseReleaseEvent

    def link_to_window(self, toggle_func: Callable[[], None]) -> None:
        self.mouseReleaseEvent = toggle_func
        self.enterEvent = lambda x: app_state.app.setOverrideCursor(QCursor(Qt.PointingHandCursor))
        self.leaveEvent = lambda x: app_state.app.setOverrideCursor(QCursor(Qt.ArrowCursor))

    def setData(self, data) -> None:
        if self.data != data:
            self.data = data

        if self.data:
            self.qr = qrcode.QRCode()
            self.qr.add_data(self.data)

            if not self.fixedSize:
                k = len(self.qr.get_matrix())
                self.setMinimumSize(k * 5, k * 5)
        else:
            self.qr = None

        self.update()

    def paintEvent(self, e):
        if not self.data:
            return

        black = QColor(0, 0, 0, 255)
        white = QColor(255, 255, 255, 255)

        if not self.qr:
            qp = QPainter()
            qp.begin(self)
            qp.setBrush(white)
            qp.setPen(white)
            r = qp.viewport()
            qp.drawRect(0, 0, r.width(), r.height())
            qp.end()
            return

        matrix = self.qr.get_matrix()
        k = len(matrix)

        qp = QPainter()
        qp.begin(self)

        r = qp.viewport()

        margin = 0
        framesize = min(r.width(), r.height())
        boxsize = max(1, int((framesize - 2 * margin) / k))
        size = k * boxsize
        left = (r.width() - size) // 2
        top = (r.height() - size) // 2

        qp.setBrush(white)
        qp.setPen(white)
        qp.drawRect(
            left - margin,
            top - margin,
            size + (margin * 2),
            size + (margin * 2)
        )

        qp.setBrush(black)
        qp.setPen(black)

        for row in range(k):
            for column in range(k):
                if matrix[row][column]:
                    qp.drawRect(
                        left + column * boxsize,
                        top + row * boxsize,
                        boxsize - 1,
                        boxsize - 1
                    )

        qp.end()


class QRDialog(WindowModalDialog):

    def __init__(self, data, parent=None, title="", show_text=False):
        WindowModalDialog.__init__(self, parent, title)

        vbox = QVBoxLayout()

        # Use a single static QR for smaller payloads.
        # Larger payloads use animated QR transport.
        STATIC_QR_MAX_BYTES = 1200

        if len(data.encode("utf-8")) <= STATIC_QR_MAX_BYTES:
            frames = [data]
        else:
            frames = create_frames(data)

        print("QR payload bytes:", len(data.encode("utf-8")))
        print("QR frame count:", len(frames))

        self.frames = frames
        self.frame_index = 0

        qrw = QRCodeWidget(frames[0])
        qscreen = QApplication.primaryScreen()
        vbox.addWidget(qrw, 1)

        frame_label = None

        if len(frames) > 1:
            frame_label = QPushButton(
                _("Frame 1 of {}".format(len(frames)))
            )
            frame_label.setEnabled(False)
            vbox.addWidget(frame_label)

        if show_text:
            text = QTextEdit()
            text.setText(data)
            text.setReadOnly(True)
            vbox.addWidget(text)

        hbox = QHBoxLayout()
        hbox.addStretch(1)

        filename = os.path.join(app_state.config.path, "qrcode.png")

        def print_qr():
            pixmap = qrw.grab()
            pixmap.save(filename, 'png')
            self.show_message(_("QR code saved to file") + " " + filename)

        def copy_to_clipboard():
            pixmap = qrw.grab()
            QApplication.clipboard().setPixmap(pixmap)
            self.show_message(_("QR code copied to clipboard"))

        b = QPushButton(_("Copy"))
        hbox.addWidget(b)
        b.clicked.connect(copy_to_clipboard)

        b = QPushButton(_("Save"))
        hbox.addWidget(b)
        b.clicked.connect(print_qr)

        b = QPushButton(_("Close"))
        hbox.addWidget(b)
        b.clicked.connect(self.accept)
        b.setDefault(True)

        vbox.addLayout(hbox)
        self.setLayout(vbox)

        # Animate multipart QR frames.
        self.animation_timer = None

        if len(frames) > 1:
            self.animation_timer = QTimer(self)
            self.animation_timer.timeout.connect(
                lambda: advance_frame()
            )

            self.animation_timer.start(250)

        def advance_frame():
            self.frame_index += 1

            if self.frame_index >= len(self.frames):
                self.frame_index = 0

            qrw.setData(self.frames[self.frame_index])

            if frame_label is not None:
                frame_label.setText(
                    _("Frame {} of {}".format(
                        self.frame_index + 1,
                        len(self.frames)
                    ))
                )

        self._qr_widget = qrw
