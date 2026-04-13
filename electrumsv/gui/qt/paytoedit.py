#!/usr/bin/env python

import time
from decimal import Decimal, InvalidOperation
from typing import List, Optional, TYPE_CHECKING

from bitcoinx import Address, cashaddr, Script, ScriptError

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFontMetrics, QTextCursor
from PyQt5.QtWidgets import QCompleter, QPlainTextEdit

from electrumsv.bitcoin import string_to_bip276_script
from electrumsv.bip276 import PREFIX_BIP276_SCRIPT
from electrumsv.constants import PREFIX_ASM_SCRIPT
from electrumsv.exceptions import InvalidPayToError
from electrumsv.i18n import _
from electrumsv.logs import logs
from electrumsv.network import Net
from electrumsv.transaction import XTxOutput
from electrumsv.web import is_URI, URIError

from .qrtextedit import ScanQRTextEdit
from . import util

if TYPE_CHECKING:
    from .send_view import SendView

logger = logs.get_logger("ui.paytoedit")

frozen_style = "QWidget { background-color:none; border:none;}"
normal_style = "QPlainTextEdit { }"


class PayToEdit(ScanQRTextEdit):
    last_cashaddr_warning = None

    def __init__(self, send_view: 'SendView') -> None:
        super().__init__()

        self._send_view = send_view
        self.document().contentsChanged.connect(self.update_size)
        self.heightMin = 0
        self.heightMax = 150
        self._completer = None
        self.textChanged.connect(self._on_text_changed)

        self._outputs: List[XTxOutput] = []
        self._errors = []

        self.is_pr = False
        self._ignore_uris = False
        self._payto_script: Optional[Script] = None

        self.update_size()

    def setFrozen(self, b):
        self.setReadOnly(b)
        self.setStyleSheet(frozen_style if b else normal_style)
        for button in self.buttons:
            button.setHidden(b)

    def set_validated(self):
        self.setStyleSheet(util.ColorScheme.GREEN.as_stylesheet(True))

    def set_expired(self):
        self.setStyleSheet(util.ColorScheme.RED.as_stylesheet(True))

    def _show_cashaddr_warning(self, address_text):
        try:
            cashaddr.decode(address_text)
        except Exception:
            return

        last_check_time = PayToEdit.last_cashaddr_warning
        ignore_watermark_time = time.time() - 24 * 60 * 60
        if last_check_time is None or last_check_time < ignore_watermark_time:
            PayToEdit.last_cashaddr_warning = time.time()

            message = _("Cash address detected. Consider using standard BSV addresses.")
            util.MessageBox.show_warning(message, title=_("Cash address warning"))


    def _parse_tx_output(self, line: str) -> XTxOutput:
        parts = [p.strip() for p in line.split(',')]

        if len(parts) < 2:
            raise InvalidPayToError(_("Invalid format (expected: address, amount): {}").format(line))

        address_part = parts[0]
        amount_part = parts[1]

        script = self._parse_output(address_part)

        try:
            amount = self._parse_amount(amount_part)
        except InvalidOperation:
            raise InvalidPayToError(_("Invalid amount: {}").format(line))

        if amount == 0:
            raise InvalidPayToError(
                _("Amount must be greater than zero (check unit mismatch: sats vs BSV)")
            )

        return XTxOutput(amount, script)




    def _parse_output(self, text: str) -> Script:
        try:
            address = Address.from_string(text, Net.COIN)
            self._show_cashaddr_warning(text)
            return address.to_script()
        except ValueError:
            pass

        if text.startswith(PREFIX_BIP276_SCRIPT + ":"):
            try:
                return string_to_bip276_script(text)
            except ValueError as e:
                raise InvalidPayToError(e.args[0])

        if text.startswith(PREFIX_ASM_SCRIPT):
            try:
                return Script.from_asm(text[len(PREFIX_ASM_SCRIPT):])
            except ScriptError as e:
                raise InvalidPayToError(e.args[0])

        raise InvalidPayToError(_("Unrecognized payment destination: {}").format(text))

    def _parse_amount(self, x):
        if x.strip() == '!':
            return all
        if not x.strip():
            raise InvalidOperation
        p = pow(10, self._send_view.amount_e.decimal_point())
        return int(p * Decimal(x.strip()))

    def setPlainText(self, text: str, ignore_uris: bool=False) -> None:
        self._ignore_uris = ignore_uris
        try:
            super().setPlainText(text)
        finally:
            self._ignore_uris = False

    def _on_text_changed(self):
        self._errors = []
        if self.is_pr:
            return

        self._payto_script = None
        self._outputs = []

        lines = [i for i in self._lines() if i]

        # SINGLE LINE MODE (script or address only)
        if len(lines) == 1:
            data = lines[0]

            if not self._ignore_uris and is_URI(data):
                self._send_view._main_window.pay_to_URI(data)
                return

            try:
                self._payto_script = self._parse_output(data)
            except InvalidPayToError:
                pass

            if self._payto_script is not None:
                self._send_view.lock_amount(False)
                return

        # MULTI-LINE MODE
        total = 0
        outputs = []
        is_max = False

        for i, line in enumerate(lines):
            try:
                tx_output = self._parse_tx_output(line)
            except InvalidPayToError as e:
                self._errors.append((i, e.args[0]))
                continue

            outputs.append(tx_output)

            if tx_output.value is all:
                is_max = True
            else:
                total += tx_output.value

        # 🔥 CRITICAL FIX: discard invalid outputs (all zero)
        if outputs and all(o.value == 0 for o in outputs):
            outputs = []

        self._send_view.set_is_spending_maximum(is_max)
        self._outputs = outputs
        self._payto_script = None

        if self._send_view.get_is_spending_maximum():
            self._send_view.do_update_fee()
        else:
            self._send_view.amount_e.setAmount(total if outputs else None)
            self._send_view.lock_amount(total or len(lines) > 1)

    def get_errors(self):
        return self._errors

    def get_payee_script(self) -> Optional[Script]:
        return self._payto_script

    def get_outputs(self, is_max):
        # SINGLE OUTPUT MODE (script pasted)
        if self._payto_script is not None:
            if is_max:
                amount = all
            else:
                amount = self._send_view.amount_e.get_amount()

            # 🔥 CRITICAL FIX: do not allow None/zero amounts
            if amount is None or amount == 0:
                return []

            return [XTxOutput(amount, self._payto_script)]

        return self._outputs[:]

    def _lines(self):
        return self.toPlainText().split('\n')

    def _is_multiline(self):
        return len(self._lines()) > 1

    def paytomany(self):
        self.setText("\n\n\n")
        self.update_size()

    def update_size(self):
        lineHeight = QFontMetrics(self.document().defaultFont()).height()
        docHeight = self.document().size().height()
        h = docHeight * lineHeight + 11
        if self.heightMin <= h <= self.heightMax:
            self.setMinimumHeight(h)
            self.setMaximumHeight(h)
        self.verticalScrollBar().hide()

    def set_completer(self, completer):
        self._completer = completer
        self._completer.setWidget(self)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self._completer.activated.connect(self._insert_completion)

    def _insert_completion(self, completion):
        if self._completer.widget() != self:
            return
        tc = self.textCursor()
        extra = len(completion) - len(self._completer.completionPrefix())
        tc.movePosition(QTextCursor.Left)
        tc.movePosition(QTextCursor.EndOfWord)
        tc.insertText(completion[-extra:])
        self.setTextCursor(tc)

    def _get_text_under_cursor(self):
        tc = self.textCursor()
        tc.select(QTextCursor.WordUnderCursor)
        return tc.selectedText()

    def keyPressEvent(self, e):
        if self.isReadOnly():
            return

        if self._completer.popup().isVisible():
            if e.key() in [Qt.Key_Enter, Qt.Key_Return]:
                e.ignore()
                return

        if e.key() in [Qt.Key_Tab]:
            e.ignore()
            return

        if e.key() in [Qt.Key_Down, Qt.Key_Up] and not self._is_multiline():
            e.ignore()
            return

        QPlainTextEdit.keyPressEvent(self, e)

    def qr_input(self):
        data = super(PayToEdit, self).qr_input(ignore_uris=True)
        if data:
            try:
                self._send_view._main_window.pay_to_URI(data)
            except URIError as e:
                self._send_view._main_window.show_error(str(e))
