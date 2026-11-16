"""Keep form controls intact when a window cannot fit their font-derived size."""
from PyQt5.QtCore import QSize, Qt
from PyQt5.QtWidgets import QFrame, QLayout, QScrollArea, QSizePolicy, QWidget


class FormScrollArea(QScrollArea):
    def __init__(self, layout, parent=None):
        super().__init__(parent)
        content = QWidget()
        content.setLayout(layout)
        layout.setSizeConstraint(QLayout.SetMinimumSize)
        content.setAutoFillBackground(False)
        self.setWidget(content)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setAlignment(Qt.AlignTop)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

    def sizeHint(self):
        hint = self.widget().sizeHint()
        if self.viewport().width() < self.widget().minimumSizeHint().width():
            hint += QSize(0, self.horizontalScrollBar().sizeHint().height())
        return hint

    def minimumSizeHint(self):
        # Scroll the form instead of squeezing fields below their minimum sizes.
        return QSize(160, 100)
