import os
import random
from typing import List

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QDialog, QLabel, QSpinBox, QVBoxLayout, QWidget,
    QComboBox, QLineEdit
)

from electrumsv.constants import RECEIVING_SUBPATH
from electrumsv.i18n import _
from electrumsv.wallet import Wallet
from electrumsv.app_state import app_state

# ✅ KEY FIX IMPORT
from electrumsv.transaction import XTxOutput, tx_output_to_display_text

from .main_window import ElectrumWindow
from .util import (
    Buttons, ButtonsTableWidget, CloseButton, FormSectionWidget,
    HelpDialogButton, MessageBox
)

MIN_SATS_PER_OUTPUT = 300
WARN_OUTPUT_COUNT = 100
HARD_CAP_OUTPUT_COUNT = 1000


class PaymentDestinationsDialog(QDialog):
    def __init__(self, main_window: ElectrumWindow, wallet: Wallet, account_id: int,
            parent: QWidget) -> None:
        super().__init__(parent, Qt.WindowSystemMenuHint | Qt.WindowTitleHint |
            Qt.WindowCloseButtonHint)

        self._main_window = main_window
        self._wallet = wallet
        self._account_id = account_id
        self._account = self._wallet.get_account(account_id)

        self.setWindowTitle(_("Payment Destinations"))
        self.setMinimumSize(500, 500)

        # --- Controls ---
        self._quantity_widget = quantity_widget = QSpinBox()
        quantity_widget.setMinimum(1)
        quantity_widget.setMaximum(1000)
        quantity_widget.setValue(10)
        quantity_widget.valueChanged.connect(self._on_quantity_changed)

        self._mode_combo = QComboBox()
        self._mode_combo.addItems([
            "Addresses only",
            "Equal split (total)",
            "Fixed amount (per address)",
            "Random split (total)"
        ])
        self._mode_combo.currentIndexChanged.connect(self._refresh)

        self._amount_input = QLineEdit()
        self._amount_input.setPlaceholderText("Amount (satoshis)")
        self._amount_input.textChanged.connect(self._refresh)

        # --- Layout ---
        vbox = QVBoxLayout()

        form = FormSectionWidget(minimum_label_width=120)
        form.add_title(_("Options"))
        form.add_row(_("How many"), quantity_widget)
        form.add_row(_("Mode"), self._mode_combo)
        form.add_row(_("Amount"), self._amount_input)
        vbox.addWidget(form)

        self._table = table = ButtonsTableWidget()
        table.addButton("icons8-copy-to-clipboard-32.png", self._on_copy_button_click,
            _("Copy all listed destinations to the clipboard"))
        table.addButton("icons8-save-as-32-windows.png", self._on_save_as_button_click,
            _("Save the listed destinations to a file"))
        table.addButton("icons8-broadcasting-32.png", self._on_send_button_click,
            _("Send to Pay-to-Many"))

        hh = table.horizontalHeader()
        hh.setStretchLastSection(True)
        vbox.addWidget(self._table, 1)

        buttons = Buttons(CloseButton(self))
        buttons.add_left_button(HelpDialogButton(self, "misc", "payment-destinations-dialog"))
        vbox.addLayout(buttons)

        self.setLayout(vbox)

        self._entries: List[str] = []

        self._refresh()

    # ------------------------
    # SAFE KEY FETCH
    # ------------------------

    def _get_receiving_keys(self, count: int):
        try:
            return self._account.get_fresh_keys(RECEIVING_SUBPATH, count)
        except Exception:
            try:
                return self._account.get_keys(RECEIVING_SUBPATH)[:count]
            except Exception as e:
                MessageBox.show_error(f"Could not get receiving addresses:\n{str(e)}")
                return []

    # ------------------------
    # Amount logic
    # ------------------------

    def _get_amounts(self, count: int) -> List[int]:
        mode = self._mode_combo.currentIndex()
        text = self._amount_input.text().strip()

        if not text.isdigit():
            return [0] * count

        value = int(text)

        if mode == 1:
            if value < count * MIN_SATS_PER_OUTPUT:
                return [0] * count
            each = max(MIN_SATS_PER_OUTPUT, value // count)
            return [each] * count

        elif mode == 2:
            if value < MIN_SATS_PER_OUTPUT:
                return [0] * count
            return [value] * count        

        elif mode == 3:
            if value < count * MIN_SATS_PER_OUTPUT:
                return [0] * count

            remaining = value
            amounts = []
            avg = value / count

            for i in range(count):
                if i == count - 1:
                    amt = max(MIN_SATS_PER_OUTPUT, remaining)
                else:
                    min_amt = max(MIN_SATS_PER_OUTPUT, int(avg * 0.5))
                    max_amt = int(avg * 1.5)
                    max_possible = remaining - (count - i - 1) * MIN_SATS_PER_OUTPUT
                    max_amt = min(max_amt, max_possible)

                    amt = random.randint(min_amt, max_amt) if max_amt >= min_amt else min_amt

                amounts.append(amt)
                remaining -= amt

            return amounts

        return [0] * count

    # ------------------------

    def _format_amount(self, satoshis: int) -> str:
        decimal_point = app_state.decimal_point
        value = satoshis / (10 ** decimal_point)
        return f"{value:.8f}".rstrip('0').rstrip('.')

    def _get_text(self) -> str:
        return os.linesep.join(self._entries)

    def _show_warning(self, prefix: str) -> None:
        MessageBox.show_warning(prefix + " " + _(
            "Note that this does not reserve the destinations."
        ))

    # ------------------------
    # Actions
    # ------------------------

    def _on_copy_button_click(self) -> None:
        self._main_window.app.clipboard().setText(self._get_text())
        self._show_warning(_("Copied to clipboard."))

    def _on_save_as_button_click(self) -> None:
        name = "payment-destinations.txt"
        filepath = self._main_window.getSaveFileName(
            _("Select where to save your destination list"), name, "*.txt")
        if filepath:
            with open(filepath, "w") as f:
                f.write(self._get_text())
        self._show_warning(_("Saved to file."))

    def _on_send_button_click(self) -> None:
        count = self._quantity_widget.value()

        if count > HARD_CAP_OUTPUT_COUNT:
            MessageBox.show_error(_("Maximum of 1000 outputs allowed."))
            return

        if count > WARN_OUTPUT_COUNT:
            MessageBox.show_warning(_(
                f"You are creating {count} outputs.\n\n"
                "Large transactions may fail or be rejected by some servers.\n"
                "Proceed with caution."
            ))

        text = self._get_text()
        account = self._wallet.get_account(self._account_id)

        self._main_window.set_active_account(account)
        self._main_window.show_send_tab()

        def apply_text():
            send_view = self._main_window.get_send_view(self._account_id)
            send_view._payto_e.paytomany()
            send_view._payto_e.setText(text)

        QTimer.singleShot(0, apply_text)
        self.close()

    # ------------------------
    # Refresh (FINAL WORKING)
    # ------------------------

    def _on_quantity_changed(self, value: int) -> None:
        self._refresh()

    def _refresh(self) -> None:
        count = self._quantity_widget.value()

        keyinstances = self._get_receiving_keys(count)
        if not keyinstances:
            return

        amounts = self._get_amounts(len(keyinstances))

        self._table.clear()
        self._table.setColumnCount(2)
        self._table.setHorizontalHeaderLabels([_("Destination"), _("Amount")])
        self._table.setRowCount(len(keyinstances))

        hh = self._table.horizontalHeader()
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(0, hh.Stretch)  # address takes remaining space
        hh.setSectionResizeMode(1, hh.ResizeToContents)  # amount auto-sized
       

        self._entries = [""] * len(keyinstances)

        from electrumsv.bitcoin import script_template_to_string

        for row, keyinstance in enumerate(keyinstances):
            try:
                address = None

                # SAME SOURCE AS RECEIVE VIEW
                template = self._account.get_script_template_for_id(
                    keyinstance.keyinstance_id
                )

                if template:
                    try:
                        # ✅ THIS IS THE ONLY CORRECT WAY
                        address = script_template_to_string(template)

                    except Exception as e:
                        address = f"[template error: {str(e)}]"

                if not address:
                    address = "[no script]"

            except Exception as e:
                address = f"[error: {str(e)}]"

            amount = amounts[row]

            if amount > 0:
                text = f"{address}, {self._format_amount(amount)}"
            else:
                text = address

            self._entries[row] = text

            self._table.setCellWidget(row, 0, QLabel(address))

            if amount > 0:
                self._table.setCellWidget(row, 1, QLabel(self._format_amount(amount)))
            else:
                self._table.setCellWidget(row, 1, QLabel(""))

