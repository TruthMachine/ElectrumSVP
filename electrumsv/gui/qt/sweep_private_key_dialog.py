from __future__ import annotations
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton,
    QMessageBox, QTextEdit, QCheckBox, QHBoxLayout
)
from PyQt5.QtCore import Qt
from typing import TYPE_CHECKING, Optional, List, Dict
import json
import logging
import math

from electrumsv.keystore import is_address_valid
from .sweep_helper import sweep_single_privkey
from electrumsv.app_state import app_state
from electrumsv.gui.qt.qrtextedit import ScanQRTextEdit

if TYPE_CHECKING:
    from .main_window import ElectrumWindow

logger = logging.getLogger("sweep")


def wallet_has_accounts(wallet) -> bool:
    for attr_name in ('has_accounts', 'accounts', '_accounts'):
        if hasattr(wallet, attr_name):
            attr = getattr(wallet, attr_name)
            if callable(attr):
                try:
                    return bool(attr())
                except Exception:
                    continue
            elif isinstance(attr, (dict, list, tuple)):
                return bool(attr)
    return False


class SweepPrivateKeyDialog(QDialog):
    def __init__(self, main_window: "ElectrumWindow") -> None:
        super().__init__(main_window)
        self.setWindowTitle("Sweep Private Key")
        self.main_window = main_window
        self.wallet = self.main_window._wallet

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Private key (WIF or BIP38):"))
        pk_row = QHBoxLayout()
        self.pk_input = QLineEdit()
        self.pk_input.setMinimumWidth(420)
        self.pk_input.textChanged.connect(self._on_privkey_changed)
        pk_row.addWidget(self.pk_input)

        self.pk_qr_btn = QPushButton("Scan QR")
        self.pk_qr_btn.clicked.connect(self._on_scan_privkey_qr)
        pk_row.addWidget(self.pk_qr_btn)
        layout.addLayout(pk_row)

        self.bip38_label = QLabel("BIP38 Passphrase:")
        layout.addWidget(self.bip38_label)
        self.bip38_input = QLineEdit()
        self.bip38_input.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.bip38_input)
        self.bip38_label.setVisible(False)
        self.bip38_input.setVisible(False)

        self.uncompressed_checkbox = QCheckBox("Use uncompressed key (for BIP38)")
        self.uncompressed_checkbox.setVisible(False)
        layout.addWidget(self.uncompressed_checkbox)

        layout.addWidget(QLabel("Destination address (required):"))
        addr_row = QHBoxLayout()
        self.addr_input = QLineEdit()
        self.addr_input.setMinimumWidth(420)
        addr_row.addWidget(self.addr_input)

        self.addr_qr_btn = QPushButton("Scan QR")
        self.addr_qr_btn.clicked.connect(self._on_scan_address_qr)
        addr_row.addWidget(self.addr_qr_btn)
        layout.addLayout(addr_row)

        self.beef_checkbox = QCheckBox("Use optional BREAD/BEEF JSON")
        self.beef_checkbox.stateChanged.connect(self.toggle_beef_input)
        layout.addWidget(self.beef_checkbox)

        self.beef_input = QTextEdit()
        self.beef_input.setPlaceholderText("Paste BREAD/BEEF JSON here...")
        self.beef_input.setVisible(False)
        layout.addWidget(self.beef_input)

        self.custom_fee_checkbox = QCheckBox("Use custom fee (sat/kB)")
        self.custom_fee_checkbox.stateChanged.connect(self.toggle_fee_input)
        layout.addWidget(self.custom_fee_checkbox)

        self.fee_input = QLineEdit()
        self.fee_input.setPlaceholderText("Enter fee in sat/kB (e.g., 100)")
        self.fee_input.setVisible(False)
        layout.addWidget(self.fee_input)

        self.sweep_button = QPushButton("Sweep")
        self.sweep_button.clicked.connect(self._on_sweep_clicked)
        layout.addWidget(self.sweep_button)

        for widget in [self.pk_input, self.addr_input, self.bip38_input, self.fee_input]:
            widget.returnPressed.connect(self.sweep_button.click)

    def toggle_beef_input(self) -> None:
        self.beef_input.setVisible(self.beef_checkbox.isChecked())
        self.adjustSize()

    def toggle_fee_input(self) -> None:
        self.fee_input.setVisible(self.custom_fee_checkbox.isChecked())
        self.adjustSize()

    def _on_privkey_changed(self) -> None:
        is_bip38 = self.pk_input.text().strip().startswith('6P')
        self.bip38_label.setVisible(is_bip38)
        self.bip38_input.setVisible(is_bip38)
        self.uncompressed_checkbox.setVisible(is_bip38)
        self.adjustSize()

    def _on_scan_privkey_qr(self) -> None:
        scanner = ScanQRTextEdit()
        data = scanner.qr_input()
        if data:
            self.pk_input.setText(data)

    def _on_scan_address_qr(self) -> None:
        scanner = ScanQRTextEdit()
        data = scanner.qr_input()
        if data:
            self.addr_input.setText(data)

    def _wipe_sensitive_data(self, *args) -> None:
        self.pk_input.clear()
        self.bip38_input.clear()
        self.fee_input.clear()

    def _confirm_low_fee(self, fee_kb: float) -> bool:
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Low Fee Warning")
        dlg.setText(
            f"The fee rate ({fee_kb} sat/kB) is below the minimum required by some miners (~100 sat/kB).\n\n"
            "Transaction confirmations may be delayed.\n\n"
            "Do you want to continue anyway?"
        )
        dlg.setIcon(QMessageBox.Warning)
        dlg.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
        dlg.setDefaultButton(QMessageBox.Cancel)
        return dlg.exec_() == QMessageBox.Yes

    def _on_sweep_clicked(self) -> None:
        def show_msg(title, msg, icon=QMessageBox.Information):
            dlg = QMessageBox(self)
            dlg.setWindowTitle(title)
            dlg.setText(msg)
            dlg.setIcon(icon)
            dlg.exec_()

        if self.wallet is None or not wallet_has_accounts(self.wallet):
            show_msg("Warning", "Wallet not loaded.", QMessageBox.Warning)
            return

        privkey = self.pk_input.text().strip()
        destination = self.addr_input.text().strip()

        if not privkey or not destination:
            show_msg("Warning", "Private key and destination required.", QMessageBox.Warning)
            return

        if not is_address_valid(destination):
            show_msg("Warning", "Invalid destination address.", QMessageBox.Warning)
            return

        daemon = getattr(app_state, 'daemon', None)
        if not daemon or not daemon.network or not daemon.network.is_connected():
            show_msg("Warning", "Network not connected.", QMessageBox.Warning)
            return

        bip38_passphrase = self.bip38_input.text().strip() if self.bip38_input.isVisible() else None
        use_uncompressed = self.uncompressed_checkbox.isChecked()
        beef_utxos = None

        if self.beef_checkbox.isChecked():
            try:
                parsed = json.loads(self.beef_input.toPlainText())
                beef_utxos = parsed["utxos"]
            except Exception as e:
                show_msg("Error", f"Invalid BEEF JSON: {e}", QMessageBox.Warning)
                return

        fee_rate_sat_per_byte = 1.0

        if self.custom_fee_checkbox.isChecked():
            try:
                fee_kb = float(self.fee_input.text())

                if fee_kb <= 0:
                    raise ValueError()

                # ✅ NEW HARD CAP GUARDRAIL
                if fee_kb > 1000:
                    show_msg(
                        "Fee Too High",
                        "Fee exceeds 1000 sat/kB.\n\n"
                        "This is unusually high and may indicate you accidentally entered "
                        "a total amount instead of a fee rate.",
                        QMessageBox.Warning
                    )
                    return

                if fee_kb < 100:
                    if not self._confirm_low_fee(fee_kb):
                        return

                fee_rate_sat_per_byte = fee_kb / 1000.0

            except Exception:
                show_msg("Warning", "Invalid fee value.", QMessageBox.Warning)
                return

        try:
            tx = sweep_single_privkey(
                wif_privkey=privkey,
                destination=destination,
                network_service=daemon.network,
                wallet=self.wallet,
                beef_utxos=beef_utxos,
                bip38_passphrase=bip38_passphrase,
                use_uncompressed=use_uncompressed,
                fee_rate_sat_per_byte=fee_rate_sat_per_byte
            )

        except Exception as e:
            logger.exception("Sweep failed")
            show_msg("Sweep Failed", str(e), QMessageBox.Critical)
            self._wipe_sensitive_data()
            return

        if not tx:
            show_msg("Sweep Failed", "No funds found.", QMessageBox.Warning)
            self._wipe_sensitive_data()
            return

        try:
            result = self.main_window.broadcast_transaction(self.wallet, tx)
            if isinstance(result, tuple) and result[0] != 1:
                show_msg("Broadcast Failed", result[1], QMessageBox.Warning)
                return

            show_msg("Success", "Transaction broadcasted.")

        except Exception as e:
            show_msg("Broadcast Failed", str(e), QMessageBox.Critical)

        finally:
            self._wipe_sensitive_data()
