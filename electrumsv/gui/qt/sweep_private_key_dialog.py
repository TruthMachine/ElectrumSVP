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


class SweepWarningDialog(QDialog):
    def __init__(self, parent=None, message: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Sweep Warning")
        layout = QVBoxLayout(self)
        label = QLabel(message)
        label.setWordWrap(True)
        layout.addWidget(label)
        ok_button = QPushButton("OK")
        ok_button.clicked.connect(self.accept)
        layout.addWidget(ok_button)


class SweepPrivateKeyDialog(QDialog):
    def __init__(self, main_window: "ElectrumWindow") -> None:
        super().__init__(main_window)
        self.setWindowTitle("Sweep Private Key")
        self.main_window = main_window
        self.wallet = self.main_window._wallet

        layout = QVBoxLayout(self)

        # Private key input
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

        # BIP38 passphrase
        self.bip38_label = QLabel("BIP38 Passphrase:")
        layout.addWidget(self.bip38_label)
        self.bip38_input = QLineEdit()
        self.bip38_input.setMinimumWidth(420)
        self.bip38_input.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.bip38_input)
        self.bip38_label.setVisible(False)
        self.bip38_input.setVisible(False)

        # Uncompressed checkbox
        self.uncompressed_checkbox = QCheckBox("Use uncompressed key (for BIP38)")
        self.uncompressed_checkbox.setVisible(False)
        layout.addWidget(self.uncompressed_checkbox)

        # Destination address input
        layout.addWidget(QLabel("Destination address (required):"))
        addr_row = QHBoxLayout()
        self.addr_input = QLineEdit()
        self.addr_input.setMinimumWidth(420)
        addr_row.addWidget(self.addr_input)

        self.addr_qr_btn = QPushButton("Scan QR")
        self.addr_qr_btn.clicked.connect(self._on_scan_address_qr)
        addr_row.addWidget(self.addr_qr_btn)
        layout.addLayout(addr_row)

        # Optional BEEF JSON
        self.beef_checkbox = QCheckBox("Use optional BEEF JSON")
        self.beef_checkbox.stateChanged.connect(self.toggle_beef_input)
        layout.addWidget(self.beef_checkbox)

        self.beef_input = QTextEdit()
        self.beef_input.setPlaceholderText("Paste BEEF JSON here...")
        self.beef_input.setMinimumWidth(420)
        self.beef_input.setMinimumHeight(120)
        self.beef_input.setVisible(False)
        layout.addWidget(self.beef_input)

        # Custom fee checkbox
        self.custom_fee_checkbox = QCheckBox("Use custom fee (sat/byte)")
        self.custom_fee_checkbox.stateChanged.connect(self.toggle_fee_input)
        layout.addWidget(self.custom_fee_checkbox)

        # Fee input, hidden by default
        self.fee_input = QLineEdit()
        self.fee_input.setPlaceholderText("Enter fee in sat/byte (e.g., 1.5)")
        self.fee_input.setMinimumWidth(200)
        self.fee_input.setVisible(False)
        layout.addWidget(self.fee_input)

        # Sweep button
        self.sweep_button = QPushButton("Sweep")
        self.sweep_button.clicked.connect(self._on_sweep_clicked)
        self.sweep_button.setDefault(True)
        self.sweep_button.setAutoDefault(True)
        layout.addWidget(self.sweep_button)

        # Make Enter trigger the sweep button
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
        """Overwrite sensitive input fields to reduce memory exposure."""
        for field in args:
            if field is not None:
                field_len = len(field)
                field = '\0' * field_len
        self.pk_input.clear()
        self.bip38_input.clear()
        self.fee_input.clear()

    def _on_sweep_clicked(self) -> None:
        if self.wallet is None or not wallet_has_accounts(self.wallet):
            QMessageBox.warning(self, "Error", "Wallet has no accounts or is not loaded.")
            return

        privkey = self.pk_input.text().strip()
        destination = self.addr_input.text().strip()
        bip38_passphrase = self.bip38_input.text().strip() if self.bip38_input.isVisible() else None
        use_uncompressed = self.uncompressed_checkbox.isChecked()
        beef_utxos: Optional[List[Dict]] = None

        # Handle optional fee
        fee_rate_sat_per_byte = 1.0
        if self.custom_fee_checkbox.isChecked() and self.fee_input.text().strip():
            try:
                fee_rate_sat_per_byte = max(0.1, float(self.fee_input.text().strip()))
            except Exception:
                QMessageBox.warning(self, "Error", "Fee must be a positive number.")
                return

        if not privkey or not destination:
            QMessageBox.warning(self, "Error", "Private key and destination are required.")
            return
        if not is_address_valid(destination):
            QMessageBox.warning(self, "Error", "Invalid destination address.")
            return

        daemon = getattr(app_state, 'daemon', None)
        if daemon is None or getattr(daemon, 'network', None) is None or not daemon.network.is_connected():
            QMessageBox.warning(self, "Error", "Network is not available or connected.")
            return

        # Handle BEEF JSON if used
        if self.beef_checkbox.isChecked():
            beef_json = self.beef_input.toPlainText().strip()
            if not beef_json:
                QMessageBox.warning(self, "Error", "BEEF JSON selected but no JSON provided.")
                return
            try:
                parsed = json.loads(beef_json)
                if "utxos" not in parsed:
                    QMessageBox.warning(self, "Error", "BEEF JSON must contain 'utxos'.")
                    return
                beef_utxos = parsed["utxos"]
            except Exception as e:
                logger.exception("Failed to parse BEEF JSON")
                QMessageBox.warning(self, "Error", f"Invalid BEEF JSON: {e}")
                return

        # Sweep transaction
        try:
            # Compute total fee satoshis by rounding after multiplying by estimated tx size
            # Sweep helper expects int fee in satoshis
            estimated_size = 10 + len(beef_utxos or []) * 148 + 34  # single output, fallback if no BEEF
            fee_satoshis = max(1, int(math.ceil(estimated_size * fee_rate_sat_per_byte)))

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
            dlg = SweepWarningDialog(self, f"Sweep failed: {str(e)}")
            dlg.exec_()
            self._wipe_sensitive_data(privkey, bip38_passphrase)
            return

        if not tx:
            dlg = SweepWarningDialog(self, "No transaction could be created (possibly no funds).")
            dlg.exec_()
            self._wipe_sensitive_data(privkey, bip38_passphrase)
            return

        # Broadcast
        try:
            result = self.main_window.broadcast_transaction(self.wallet, tx)
            if isinstance(result, tuple) and len(result) >= 2 and result[0] != 1:
                dlg = SweepWarningDialog(self, f"Transaction could not be broadcast:\n{result[1]}")
                dlg.exec_()
                self._wipe_sensitive_data(privkey, bip38_passphrase)
                return
            QMessageBox.information(self, "Sweep Successful", "Transaction broadcasted successfully!")
        except Exception as e:
            logger.exception("Broadcast failed")
            err_msg = str(e)
            if "Missing inputs" in err_msg or "transaction was rejected" in err_msg:
                dlg = SweepWarningDialog(self, "The transaction could not be broadcast. "
                                               "This usually happens if the UTXOs are already spent or invalid.")
                dlg.exec_()
            else:
                QMessageBox.critical(self, "Broadcast Failed", f"Broadcast failed: {err_msg}")
        finally:
            self._wipe_sensitive_data(privkey, bip38_passphrase)

