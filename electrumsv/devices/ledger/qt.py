from typing import Dict, Any, Optional

from PyQt5.QtCore import pyqtSignal, QElapsedTimer, QTimer
from PyQt5 import sip
from PyQt5.QtWidgets import QInputDialog, QLineEdit, QVBoxLayout, QLabel, QProgressBar

from electrumsv.i18n import _
from electrumsv.keystore import Hardware_KeyStore

from electrumsv.gui.qt.main_window import ElectrumWindow
from electrumsv.gui.qt.util import WindowModalDialog

from .ledger import LedgerPlugin, Ledger_KeyStore
from ..hw_wallet.qt import QtHandlerBase, QtPluginBase, HandlerWindow


class Plugin(LedgerPlugin, QtPluginBase):
    icon_paired = "icons8-usb-connected-80.png"
    icon_unpaired = "icons8-usb-disconnected-80.png"

    def create_handler(self, window: HandlerWindow) -> QtHandlerBase:
        return Ledger_Handler(window)

    def show_settings_dialog(self, window: ElectrumWindow, keystore: Hardware_KeyStore) -> None:
        assert keystore.handler is not None
        keystore.handler.setup_dialog()


class Ledger_Handler(QtHandlerBase):
    setup_signal = pyqtSignal()
    auth_signal = pyqtSignal(object, object)
    progress_signal = pyqtSignal(object, int, int)
    stage_signal = pyqtSignal(str, int, int)

    def __init__(self, win) -> None:
        super(Ledger_Handler, self).__init__(win, 'Ledger')
        self._signing_dialog = None
        self._cancel_event = None
        self.stage_signal.connect(self.stage_dialog)
        self.setup_signal.connect(self.setup_dialog)
        self.auth_signal.connect(self.auth_dialog)
        self.progress_signal.connect(self.progress_dialog)
        self._operation_clock = QElapsedTimer()
        self._operation_timer = QTimer(self)
        self._operation_timer.setInterval(1000)
        self._operation_timer.timeout.connect(self._update_elapsed)

    def word_dialog(self, msg) -> None:
        response = QInputDialog.getText(self.top_level_window(),
            _("Ledger Wallet Authentication"), msg, QLineEdit.Password)
        if not response[1]:
            self.word = None
        else:
            self.word = str(response[0])
        self.done.set()

    def message_dialog(self, msg, _on_cancel=None) -> None:
        if self._signing_dialog is not None:
            self._signing_dialog.set_message(msg)
            return
        if self.dialog is not None and not sip.isdeleted(self.dialog):
            self._message_label.setText(msg)
            self._progress.setRange(0, 0)
            return
        self.clear_dialog()
        self.dialog = dialog = WindowModalDialog(self.top_level_window(), _("Ledger Status"))
        l = QLabel(msg)
        self._message_label = l
        l.setWordWrap(True)
        vbox = QVBoxLayout(dialog)
        vbox.addWidget(l)
        self._progress = QProgressBar(dialog)
        self._progress.setRange(0, 0)
        vbox.addWidget(self._progress)
        self._elapsed_label = QLabel(dialog)
        vbox.addWidget(self._elapsed_label)
        self._operation_clock.start()
        self._operation_timer.start()
        self._update_elapsed()
        dialog.show()

    def show_progress(self, message, current, total):
        self.progress_signal.emit(message, current, total)

    def progress_dialog(self, message, current, total):
        if self._signing_dialog is not None:
            self._signing_dialog.set_message(message)
            return
        self.message_dialog(message)
        self._progress.setRange(0, total)
        self._progress.setValue(max(0, current - 1))

    def _update_elapsed(self):
        if self.dialog is None or sip.isdeleted(self.dialog):
            self._operation_timer.stop()
            return
        seconds = self._operation_clock.elapsed() // 1000
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        self._elapsed_label.setText(_('Elapsed: {}').format(
            '{:02d}:{:02d}:{:02d}'.format(hours, minutes, seconds)))

    def clear_dialog(self):
        if getattr(self, "_signing_dialog", None) is not None:
            return  # The operation owner closes it after host signature verification.
        timer = getattr(self, '_operation_timer', None)
        if timer is not None:
            timer.stop()
        super().clear_dialog()

    def bind_signing_dialog(self, dialog):
        if dialog is not None:
            self.clear_dialog()
        self._signing_dialog = dialog
        self._cancel_event = dialog.cancel_event if dialog is not None else None

    def check_cancelled(self):
        if self._cancel_event is not None and self._cancel_event.is_set():
            from electrumsv.exceptions import UserCancelled
            raise UserCancelled()

    def show_stage(self, stage, completed=0, total=0):
        self.stage_signal.emit(stage, completed, total)

    def stage_dialog(self, stage, completed, total):
        if self._signing_dialog is not None:
            self._signing_dialog.stage(stage, completed, total)

    def win_yes_no_question(self, msg):
        if self._signing_dialog is None:
            return super().win_yes_no_question(msg)
        def answer(accepted):
            self._ok = int(accepted)
            self.done.set()
        self._signing_dialog.ask(msg, answer)

    def error_dialog(self, msg, blocking=False):
        if self._signing_dialog is None:
            return super().error_dialog(msg)
        self._signing_dialog.set_message(msg)
        self.done.set()

    def warning_dialog(self, msg):
        if self._signing_dialog is None:
            return super().warning_dialog(msg)
        self._signing_dialog.set_message(msg)
        self.done.set()

    def auth_dialog(self, keystore: Ledger_KeyStore, data: Dict[str, Any]) -> None:
        try:
            from .auth2fa import LedgerAuthDialog
        except ImportError as e:
            self.message_dialog(str(e))
            return
        dialog = LedgerAuthDialog(self, keystore, data)
        dialog.exec_()
        self.word = dialog.pin
        self.done.set()

    def get_auth(self, keystore: Ledger_KeyStore, data: Dict[str, Any]) -> Optional[str]:
        self.done.clear()
        self.auth_signal.emit(keystore, data)
        self.done.wait()
        return self.word

    def get_setup(self) -> None:
        self.done.clear()
        self.setup_signal.emit()
        self.done.wait()

    def setup_dialog(self) -> None:
        #from btchip.btchipPersoWizard import StartBTChipPersoDialog
        #dialog = StartBTChipPersoDialog()
        #dialog.exec_()
        # rt12 -- Ledger settings use PyQt4, which we do not have.
        self.show_error(_('This button does nothing.'))
