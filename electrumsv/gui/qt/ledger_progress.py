"""One window owns the options, questions and progress of a Ledger signing job."""
from threading import Event

from PyQt5.QtCore import QElapsedTimer, QSize, Qt, QTimer
from PyQt5.QtWidgets import (QGridLayout, QLabel, QPlainTextEdit, QProgressBar,
    QPushButton, QWidget)

from electrumsv.app_state import app_state
from electrumsv.exceptions import UserCancelled
from electrumsv.i18n import _
from .hardware import LedgerSigningOptionsDialog


class LedgerSigningDialog(LedgerSigningOptionsDialog):
    def __init__(self, parent, tx, handlers, operation, callback):
        super().__init__(parent, tx)
        self.setWindowTitle(_('Transaction signing'))
        self.handlers = handlers
        self.operation, self.callback = operation, callback
        self.cancel_event = Event()
        self.running = False
        self.completed = False
        self._question = None
        self._reported = False
        self._success = False
        self._stage = None
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._elapsed)
        self._bars = {}
        self._statuses = {}
        self._skipped = set()

        steps = QWidget()
        grid = QGridLayout(steps)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(5)
        for row, (stage, title) in enumerate((
                ('prepare', _('Prepare transaction')),
                ('parents', _('Verify previous transactions')),
                ('review', _('Review on device')),
                ('sign', _('Sign inputs')),
                ('verify', _('Verify signatures')))):
            label = QLabel(title)
            label.setWordWrap(True)
            bar = QProgressBar()
            bar.setRange(0, 1)
            bar.setValue(0)
            bar.setFormat(_('Waiting'))
            status = QLabel(_('Waiting'))
            status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            bar.setTextVisible(False)
            bar.setFixedHeight(6)
            bar.setStyleSheet('QProgressBar { border: none; border-radius: 3px; '
                'background: palette(mid); } QProgressBar::chunk { '
                'border-radius: 3px; background: palette(highlight); }')
            grid.addWidget(label, row * 3, 0)
            grid.addWidget(status, row * 3, 1)
            grid.addWidget(bar, row * 3 + 1, 0, 1, 2)
            grid.setRowMinimumHeight(row * 3 + 2, 6)
            self._bars[stage] = bar
            self._statuses[stage] = status
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setFixedHeight(self.fontMetrics().lineSpacing() * 4 + 12)
        self.details.setFocusPolicy(Qt.NoFocus)
        self.details.setPlainText(_('Keep your Ledger connected throughout signing.'))
        self.elapsed = QLabel(_('Elapsed: {}').format('00:00:00'))
        self.layout().insertWidget(3, steps)
        self.layout().insertWidget(4, self.details)
        self.layout().insertWidget(5, self.elapsed)
        self.proceed_button.clicked.disconnect()
        self.proceed_button.clicked.connect(self._proceed)
        self.cancel_button = next(b for b in self.findChildren(QPushButton)
            if b is not self.proceed_button)
        self.setFixedSize(self.sizeHint().expandedTo(QSize(600, 540)))

    def _proceed(self):
        if self._question is not None:
            answer, self._question = self._question, None
            self.proceed_button.setEnabled(False)
            answer(True)
        elif not self.running and not self.completed:
            self.running = True
            self.verify_inputs.setEnabled(False)
            self.proceed_button.setEnabled(False)
            self.note.setText(_('Follow the instructions on your Ledger.'))
            self._fit_note()
            self._clock.start()
            self._timer.start()
            self.stage('prepare')
            for handler in self.handlers:
                handler.bind_signing_dialog(self)
            try:
                app_state.app.run_in_thread(
                    lambda: self.operation(self.verify_inputs.isChecked()), on_done=self._done)
            except Exception as exc:
                from concurrent.futures import Future
                future = Future()
                future.set_exception(exc)
                self._done(future)

    def stage(self, stage, completed=0, total=0):
        if not self.running or stage not in self._bars:
            return
        if self._stage != stage and self._stage is not None:
            previous = self._bars[self._stage]
            if self._stage not in self._skipped:
                previous.setRange(0, 1)
                previous.setValue(1)
                previous.setFormat(_('Complete'))
                self._statuses[self._stage].setText(_('Complete'))
        self._stage = stage
        bar = self._bars[stage]
        if total < 0:
            self._skipped.add(stage)
            bar.setRange(0, 1)
            bar.setValue(0)
            bar.setFormat(_('Skipped'))
            self._statuses[stage].setText(_('Skipped'))
        elif total:
            bar.setRange(0, total)
            bar.setValue(completed)
            bar.setFormat('%v / %m')
            self._statuses[stage].setText('{} / {}'.format(completed, total))
        else:
            bar.setRange(0, 0)
            self._statuses[stage].setText(_('In progress'))

    def set_message(self, message):
        if not self.completed:
            self.details.setPlainText(str(message))

    def ask(self, message, answer):
        if self.cancel_event.is_set():
            answer(False)
            return
        self._question = answer
        self.set_message(message)
        self.proceed_button.setEnabled(True)
        self.proceed_button.setFocus()

    def _elapsed(self):
        seconds = self._clock.elapsed() // 1000
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        self.elapsed.setText(_('Elapsed: {}').format(
            '{:02d}:{:02d}:{:02d}'.format(hours, minutes, seconds)))

    def _done(self, future):
        self.running = False
        self.completed = True
        self._timer.stop()
        self._elapsed()
        for handler in self.handlers:
            handler.bind_signing_dialog(None)
        try:
            future.result()
        except Exception as exc:
            self.details.setPlainText(_('Signing cancelled.') if isinstance(exc, UserCancelled)
                else str(exc))
            if self._stage is not None:
                bar = self._bars[self._stage]
                bar.setRange(0, 1)
                bar.setValue(0)
                bar.setFormat(_('Stopped'))
                self._statuses[self._stage].setText(_('Stopped'))
            self.proceed_button.hide()
            self.cancel_button.setText(_('Close'))
            self.cancel_button.setEnabled(True)
            self.cancel_button.setDefault(True)
        else:
            self._success = True
            for stage, bar in self._bars.items():
                if stage not in self._skipped:
                    bar.setRange(0, 1)
                    bar.setValue(1)
                    bar.setFormat(_('Complete'))
                    self._statuses[stage].setText(_('Complete'))
            self.details.setPlainText(_('Signatures verified.'))
            # Close before the caller continues (which may open a broadcast UI).
            QTimer.singleShot(700, self._finish)

    def _finish(self):
        if self._reported:
            return
        self._reported = True
        self.accept()
        self.callback(self._success)

    def reject(self):
        if self.running:
            self.cancel_event.set()
            if self._question is not None:
                answer, self._question = self._question, None
                answer(False)
            self.cancel_button.setEnabled(False)
            self.proceed_button.setEnabled(False)
            self.details.setPlainText(_('Cancellation requested. Finish or reject the current '
                'request on your Ledger; signing will stop before the next device operation.'))
        else:
            self._finish()

    def closeEvent(self, event):
        if self.running:
            self.reject()
            event.ignore()
        else:
            self._finish()
            event.accept()
