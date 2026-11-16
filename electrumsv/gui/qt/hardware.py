"""Run device I/O while keeping Qt handlers and dialogs on the GUI thread."""
from dataclasses import replace
from PyQt5 import sip
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtWidgets import (QAction, QLabel, QVBoxLayout, QHBoxLayout, QPushButton,
    QTreeWidget, QTreeWidgetItem, QLineEdit, QWidget, QCheckBox, QToolButton, QPlainTextEdit, QDialog)

from electrumsv.app_state import app_state
from electrumsv.exceptions import UserCancelled
from electrumsv.i18n import _
from electrumsv.devices.hardware import DeviceCheckError
from .util import WindowModalDialog, read_QIcon


def device_status_text(status):
    if status.verified_xpubs:
        return _('Account key matches')
    if status.check_error and status.state in ('connected', 'bch_ready'):
        return _('Unavailable')
    return {'not_detected': _('Not detected'), 'unavailable': _('Unavailable'),
        'connected': _('Connected; pairing unchecked'), 'locked': _('Locked'),
        'bch_ready': _('Bitcoin Cash open'), 'app_not_open': _('Open Bitcoin Cash to sign')}[status.state]


def show_unavailable_hardware(window, keystore):
    from .hardware_status import hardware_toolbar_action
    hardware_toolbar_action(window)



class DeviceOperationDialog(WindowModalDialog):
    def __init__(self, parent, operation):
        super().__init__(parent, _('Hardware wallet'))
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        self._operation = operation
        self._finished = False
        self._result = None
        self._error = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(_('Follow the instructions on your device.\n'
            'To cancel, reject the request on the device.')))
        QTimer.singleShot(0, self._start)

    def _start(self):
        try:
            app_state.app.run_in_thread(self._operation, on_done=self._done)
        except Exception as exc:
            self._error = exc
            self._finished = True
            self.accept()

    def _done(self, future):
        try:
            self._result = future.result()
        except Exception as exc:
            self._error = exc
        self._finished = True
        self.accept()

    def reject(self):
        # A running USB call cannot safely be cancelled by abandoning its dialogs.
        if self._finished:
            super().reject()

    def closeEvent(self, event):
        if not self._finished:
            event.ignore()
        else:
            super().closeEvent(event)

    def run(self):
        self.exec_()
        if self._error is not None:
            raise self._error
        return self._result


def run_device_operation(parent, operation):
    return DeviceOperationDialog(parent, operation).run()


class HardwareWalletDialog(QDialog):
    """Candidates → Connect/check → Select. A highlighted row alone is not a selection."""
    def __init__(self, parent, stores=(), monitor=None):
        super().__init__(parent, Qt.Window | Qt.WindowTitleHint |
            Qt.WindowSystemMenuHint | Qt.WindowCloseButtonHint)
        self.setWindowTitle(_('Hardware wallets'))
        self._closed = False
        self._connecting = False
        self.finished.connect(self._on_finished)
        self.stores = stores
        self.monitor = monitor
        self._checked = None
        self._loading = False
        self.setMinimumSize(620, 360)
        self.resize(700, 420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        intro = QLabel(_('Choose a detected device, connect it, then select it after the account key check passes.'))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.devices = QTreeWidget()
        self.devices.setObjectName('hardwareDevices')
        from electrumsv.util import resource_path
        check_icon = resource_path('icons', 'control-check.svg').replace('\\', '/')
        self.devices.setStyleSheet('QTreeWidget#hardwareDevices::indicator { width: 16px; '
            'height: 16px; border: 1px solid palette(mid); border-radius: 3px; } '
            'QTreeWidget#hardwareDevices::indicator:checked { background: palette(highlight); '
            'image: url("' + check_icon + '"); }')
        self.devices.setHeaderLabels([_('Device'), _('Status'), _('Application / version')])
        self.devices.setRootIsDecorated(False)
        self.devices.setUniformRowHeights(True)
        from PyQt5.QtWidgets import QHeaderView
        self.devices.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.devices.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.devices.header().setMinimumSectionSize(120)
        self.devices.currentItemChanged.connect(self._selected)
        layout.addWidget(self.devices)
        self.details = QLabel(_('Connect and unlock a hardware wallet to begin.'))
        self.details.setTextFormat(Qt.PlainText)
        self.details.setWordWrap(True)
        self.details.setMinimumHeight(48)
        layout.addWidget(self.details)
        self.diagnostics_toggle = QToolButton()
        self.diagnostics_toggle.setText(_('Connection details'))
        self.diagnostics_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.diagnostics_toggle.setArrowType(Qt.RightArrow)
        self.diagnostics_toggle.setCheckable(True)
        layout.addWidget(self.diagnostics_toggle)
        self.diagnostics = QPlainTextEdit()
        self.diagnostics.setReadOnly(True)
        self.diagnostics.setMaximumHeight(100)
        self.diagnostics.hide()
        self.diagnostics_toggle.toggled.connect(self._toggle_diagnostics)
        layout.addWidget(self.diagnostics)
        buttons = QHBoxLayout()
        self.refresh_button = QPushButton(_('Refresh'))
        self.refresh_button.setAutoDefault(False)
        self.refresh_button.clicked.connect(self.refresh)
        buttons.addWidget(self.refresh_button)
        self.connect_button = QPushButton(_('Connect'))
        self.connect_button.setEnabled(False)
        self.connect_button.setAutoDefault(False)
        self.connect_button.clicked.connect(self.connect_selected)
        buttons.addWidget(self.connect_button)
        buttons.addStretch(1)
        close = QPushButton(_('Cancel'))
        close.setAutoDefault(False)
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        self.select_button = QPushButton(_('Select'))
        self.select_button.setDefault(True)
        self.select_button.setEnabled(False)
        self.select_button.clicked.connect(self.select_checked)
        buttons.addWidget(self.select_button)
        layout.addLayout(buttons)
        if monitor is not None:
            monitor.changed.connect(self.show_rows)

    def _toggle_diagnostics(self, shown):
        self.diagnostics.setVisible(shown)
        self.diagnostics_toggle.setArrowType(Qt.DownArrow if shown else Qt.RightArrow)

    def _selected(self, current, previous):
        if self._loading:
            return
        status = current.data(0, Qt.UserRole) if current is not None else None
        self._checked = status if status is not None and status.verified_xpubs else None
        self.select_button.setEnabled(self._checked is not None)
        self.connect_button.setEnabled(status is not None)
        if status is not None:
            self.details.setText(_('Account public key checked. It will be checked again before signing.')
                if self._checked else status.check_error or status.detail)
        else:
            self.details.setText(_('Choose a device and connect it to check the account public key.'))

    def _on_finished(self, result):
        self._closed = True
        if self.monitor:
            self.monitor.changed.disconnect(self.show_rows)
            self.monitor.resume()
        handler = getattr(self, '_connection_handler', None)
        if handler is not None:
            handler.clear_dialog()

    def _connection_message(self, message, on_cancel=None):
        if not self._closed:
            self.details.setText(message)

    def connect_selected(self):
        item = self.devices.currentItem()
        if item is None or self._connecting:
            return
        status = replace(item.data(0, Qt.UserRole), verified_xpubs=())
        self._checked = None
        self._connecting = True
        item.setData(0, Qt.UserRole, status)
        item.setCheckState(0, Qt.Unchecked)
        self.select_button.setEnabled(False)
        self.connect_button.setEnabled(False)
        self.devices.setEnabled(False)
        self.refresh_button.setEnabled(False)
        if self.monitor:
            self.monitor.pause()
        generation = self.monitor._generation if self.monitor else None
        stores = tuple(self.stores)
        support = app_state.device_manager.hardware
        try:
            handler = self._connection_handler = support.plugin(status.kind).create_handler(self.parent())
            if status.kind == 'ledger':
                # Show app-opening progress inline, so the picker stays dismissible.
                handler.message_signal.disconnect()
                handler.message_signal.connect(self._connection_message)
        except Exception as exc:
            self._connecting = False
            self.devices.setEnabled(True)
            self.refresh_button.setEnabled(True)
            self.connect_button.setEnabled(True)
            self.details.setText(str(exc))
            if self.monitor:
                self.monitor.resume()
            return

        def done(future):
            if sip.isdeleted(self) or self._closed:
                return
            self._connecting = False
            self.devices.setEnabled(True)
            self.refresh_button.setEnabled(True)
            self.connect_button.setEnabled(True)
            if self.monitor and generation != self.monitor._generation:
                self.monitor.resume()
                self.monitor.refresh()
                return
            failed_status = status
            try:
                checked = future.result()
                item.setData(0, Qt.UserRole, checked)
                item.setCheckState(0, Qt.Checked)
                item.setText(1, _('Account key matches'))
                item.setText(2, ' '.join(value for value in (checked.app, checked.version) if value))
                self._selected(item, None)
                if self.monitor:
                    self.monitor.record_check(checked)
            except UserCancelled:
                self.details.setText(_('Connection cancelled. Select the device and retry.'))
                failed_status = replace(status, check_error=self.details.text())
            except DeviceCheckError as exc:
                failed_status = exc.status
                self.details.setText(str(exc))
            except Exception as exc:
                self.details.setText(str(exc))
                failed_status = replace(status, check_error=str(exc))
            finally:
                if self._checked is None:
                    item.setData(0, Qt.UserRole, failed_status)
                    item.setText(1, device_status_text(failed_status))
                    if self.monitor:
                        self.monitor.record_check(failed_status)
                if self.monitor:
                    self.monitor.resume()

        def start_connection():
            if sip.isdeleted(self) or self._closed:
                return
            if self.monitor and generation != self.monitor._generation:
                self._connecting = False
                self.devices.setEnabled(True)
                self.refresh_button.setEnabled(True)
                self.connect_button.setEnabled(True)
                self.monitor.resume()
                self.monitor.refresh()
                return
            app_state.app.run_in_thread(
                lambda: support.check_device(status, stores, handler), on_done=done)
        if self.monitor:
            self.monitor.when_idle(start_connection)
        else:
            start_connection()

    def select_checked(self):
        if self._checked is not None:
            app_state.device_manager.hardware.selected_device = self._checked
            self.accept()

    def show_rows(self, rows):
        if not self.isVisible() and self.monitor is not None:
            return
        if self.monitor:
            self.stores = self.monitor.stores
        current = self.devices.currentItem()
        current_key = ((current.data(0, Qt.UserRole).kind,
            current.data(0, Qt.UserRole).device.id_) if current else None)
        existing = {}
        for index in range(self.devices.topLevelItemCount()):
            item = self.devices.topLevelItem(index)
            old = item.data(0, Qt.UserRole)
            existing[(old.kind, old.device.id_)] = item
        self._loading = True
        present = set()
        diagnostics = []
        for row in rows:
            if row.device is None:
                if row.state == 'unavailable':
                    diagnostics.append('{}: {}'.format(row.label, row.detail))
                continue
            key = (row.kind, row.device.id_)
            present.add(key)
            item = existing.get(key)
            if item is None:
                item = QTreeWidgetItem()
                self.devices.addTopLevelItem(item)
            for column, text in enumerate((row.label, device_status_text(row),
                    ' '.join(value for value in (row.app, row.version) if value))):
                if item.text(column) != text:
                    item.setText(column, text)
            # The tick is a result, not a user-settable checkbox.
            item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Checked if row.verified_xpubs else Qt.Unchecked)
            item.setData(0, Qt.UserRole, row)
            item.setToolTip(0, row.label)
            item.setToolTip(1, row.check_error or row.detail)
        for key, item in existing.items():
            if key not in present:
                self.devices.takeTopLevelItem(self.devices.indexOfTopLevelItem(item))
        if current_key not in present:
            self.devices.setCurrentItem(None)
            self.devices.clearSelection()
        self.diagnostics.setPlainText('\n\n'.join(diagnostics))
        self.diagnostics_toggle.setVisible(bool(diagnostics))
        if not diagnostics:
            self.diagnostics_toggle.setChecked(False)
        self._loading = False
        self._selected(self.devices.currentItem(), None)
        if not self.devices.topLevelItemCount():
            self.details.setText(_('No devices detected. Connect a hardware wallet; this list updates automatically.'))

    def refresh(self):
        if self.monitor:
            self.monitor.refresh()
            return
        self.refresh_button.setEnabled(False)
        try:
            rows = run_device_operation(self, lambda: app_state.device_manager.hardware.inspect(self.stores))
            self.show_rows(rows)
        except Exception as exc:
            self.details.setText(str(exc))
        finally:
            self.refresh_button.setEnabled(True)


def show_hardware_wallets(parent):
    existing = getattr(parent, '_hardware_dialog', None)
    if existing is not None and not sip.isdeleted(existing) and existing.isVisible():
        existing.raise_()
        existing.activateWindow()
        return
    account = getattr(parent, '_account', None)
    monitor = getattr(parent, '_hardware_monitor', None)
    dialog = HardwareWalletDialog(parent, account.get_keystores() if account is not None else (), monitor)
    parent._hardware_dialog = dialog
    dialog.setAttribute(Qt.WA_DeleteOnClose, True)
    dialog.show()
    if monitor is not None:
        dialog.show_rows(monitor.rows)
    dialog.refresh()


class MessageHashReview(QWidget):
    """Digest shown by Ledger's BCH app; this is not the Bitcoin message-signing digest."""
    def __init__(self, message_edit):
        super().__init__()
        import hashlib
        layout = QVBoxLayout(self)
        note = QLabel(_('Ledger may display only a message hash. Compare it with this SHA-256 '
            'of the exact UTF-8 message, including spaces and newlines. '
            'The device has not displayed the message text.'))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.digest = QLineEdit()
        self.digest.setReadOnly(True)
        layout.addWidget(self.digest)
        def update():
            self.digest.setText(hashlib.sha256(message_edit.toPlainText().encode('utf8')).hexdigest().upper())
        message_edit.textChanged.connect(update)
        update()


class LedgerSigningOptionsDialog(WindowModalDialog):
    """Per-operation policy; never persist a reduced-verification preference."""
    def __init__(self, parent, tx):
        from PyQt5.QtWidgets import QLayout, QSizePolicy
        super().__init__(parent, _('Ledger signing options'))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        layout.setSizeConstraint(QLayout.SetMinimumSize)
        summary = QLabel(_('Inputs: {} · Outputs: {}').format(len(tx.inputs), len(tx.outputs)))
        summary.setWordWrap(True)
        layout.addWidget(summary)
        self.verify_inputs = QCheckBox(_('Verify input amounts (recommended)'))
        self.verify_inputs.setChecked(True)
        layout.addWidget(self.verify_inputs)
        self.note = QLabel()
        self.note.setTextFormat(Qt.PlainText)
        self.note.setWordWrap(True)
        self.note.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        layout.addWidget(self.note)
        self.verify_inputs.toggled.connect(self._update_note)
        self._update_note(True)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        cancel = QPushButton(_('Cancel'))
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        self.proceed_button = QPushButton(_('Continue to device'))
        self.proceed_button.setDefault(True)
        self.proceed_button.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.proceed_button)
        layout.addSpacing(4)
        layout.addLayout(buttons)
        self.setMinimumWidth(500)
        self.resize(540, self.sizeHint().height())

    def _update_note(self, enabled):
        self.note.setText(_('Ledger checks previous transactions before signing. '
            'Large transactions may take several minutes.') if enabled else
            _('Ledger may show “unverified inputs” and cannot verify the fee. '
                'Signing many inputs may still be slow.'))
        self._fit_note()

    def _fit_note(self):
        # Keep wrapped lines above the buttons even at larger fonts or in longer
        # translations. A fixed dialog height must never hide the fee warning.
        width = max(460, self.width() - self.layout().contentsMargins().left() -
            self.layout().contentsMargins().right())
        self.note.setMinimumHeight(max(self.note.fontMetrics().height(),
            self.note.heightForWidth(width)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_note()


class SigningInputsDialog(WindowModalDialog):
    """Choose signatures, while retaining the complete transaction for device review."""
    def __init__(self, parent, tx, stores):
        super().__init__(parent, _('Choose inputs to sign'))
        from electrumsv.transaction import NO_SIGNATURE
        layout = QVBoxLayout(self)
        note = QLabel(_('Only checked inputs will receive new signatures. Existing signatures '
            'will be kept. Ledger still reviews the whole transaction: each new signature uses '
            'SIGHASH_ALL | FORKID (0x41), committing to all inputs and outputs. '
            'Save the incomplete transaction as JSON to pass it to another signer.'))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.inputs = QTreeWidget()
        self.inputs.setHeaderLabels([_('Input'), _('Amount (satoshis)'), _('Outpoint')])
        for index, txin in enumerate(tx.inputs):
            if txin.is_complete() or not any(
                    signature == NO_SIGNATURE and store.is_signature_candidate(key)
                    for key, signature in zip(txin.x_pubkeys, txin.signatures) for store in stores):
                continue
            item = QTreeWidgetItem([str(index + 1), str(txin.value),
                '{}:{}'.format(txin.prev_hash[::-1].hex(), txin.prev_idx)])
            item.setData(0, Qt.UserRole, index)
            item.setToolTip(2, item.text(2))
            item.setCheckState(0, Qt.Checked)
            self.inputs.addTopLevelItem(item)
        self.inputs.resizeColumnToContents(0)
        self.inputs.resizeColumnToContents(1)
        layout.addWidget(self.inputs)
        buttons = QHBoxLayout()
        cancel = QPushButton(_('Cancel'))
        cancel.clicked.connect(self.reject)
        self.sign_button = QPushButton(_('Continue to device'))
        self.sign_button.clicked.connect(self.accept)
        self.inputs.itemChanged.connect(lambda: self.sign_button.setEnabled(bool(self.selected_indexes())))
        self.sign_button.setEnabled(bool(self.selected_indexes()))
        buttons.addWidget(cancel)
        buttons.addWidget(self.sign_button)
        layout.addLayout(buttons)
        self.resize(720, 360)

    def selected_indexes(self):
        return {self.inputs.topLevelItem(i).data(0, Qt.UserRole)
            for i in range(self.inputs.topLevelItemCount())
            if self.inputs.topLevelItem(i).checkState(0) == Qt.Checked}
