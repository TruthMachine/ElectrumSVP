"""Hardware account import pages; vendor I/O lives behind HardwareWallets."""
from __future__ import annotations

import concurrent.futures
from typing import Any, List, Optional, Sequence, Tuple, TYPE_CHECKING

from bitcoinx import bip32_decompose_chain_string
from PyQt5 import sip
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QLineEdit, QProgressBar,
    QPushButton, QTextEdit, QVBoxLayout, QWidget, QWizard, QWizardPage)

from electrumsv.app_state import app_state
from electrumsv.bitcoin import compose_chain_string
from electrumsv.constants import DerivationType
from electrumsv.device import DeviceInfo
from electrumsv.exceptions import UserCancelled
from electrumsv.i18n import _
from electrumsv.keystore import bip44_derivation_cointype, instantiate_keystore
from electrumsv.logs import logs
from .hardware import run_device_operation
from .util import ChoicesLayout, icon_path, MessageBox, protected

if TYPE_CHECKING:
    from .account_wizard import AccountWizard
    from .main_window import ElectrumWindow

logger = logs.get_logger('hardware-accounts')

NO_DEVICES_FOUND_TEXT = _("A scan has been unable to locate any connected hardware wallets. "
    "Connect and unlock your hardware wallet, close other wallet applications, then "
    "press the 'Rescan' button to try again.") +"\n\n"+ _("If any problems were encountered "
    "during the scan, they will be displayed below. If there are no indications why your "
    "hardware wallet is not being found, press the 'Help' button for further direction.")

DEVICE_SETUP_ERROR_TEXT = _("The selected device is not ready. Unlock it on its own screen, "
    "close other wallet applications, then choose Try again. Ledger can request opening "
    "Bitcoin Cash; confirm on the device if asked. To select a different device, go back.")

DEVICE_SETUP_SUCCESS_TEXT = _("Connected to your {} hardware wallet.") + "\n\n" + _(
    "Choose the account derivation path. When reopening an existing account, use its original "
    "path: a different account path gives different addresses even on the same device.") + "\n\n" + _(
    "The default {} matches traditional BTC/BSV accounts. BCH accounts commonly use "
    "m/44'/145'/0'. Keep the original path when checking an existing balance.")



class FindHardwareWalletAccountPage(QWizardPage):
    _selected_device: Optional[Tuple[str, DeviceInfo]]
    _devices: List[Tuple[str, DeviceInfo]]
    _device_debug_message: Optional[str]
    _alive: bool = False

    def __init__(self, wizard: AccountWizard):
        super().__init__(wizard)

        self._devices = []
        self._selected_device = None
        self._device_debug_message = None
        self._scan_generation = 0

        self.setTitle(_("Import a Hardware Wallet as an Account"))
        self.setFinalPage(False)

    # Qt method called to get the Id of the next page.
    def nextId(self):
        from .account_wizard import AccountPage
        return AccountPage.SETUP_HARDWARE_WALLET

    # Qt method called when 'Next' or 'Finish' is clicked for last-minute validation.
    def validatePage(self) -> bool:
        wizard: AccountWizard = self.wizard()
        wizard.set_selected_device(self._selected_device)
        return True

    # Qt method called to determine if 'Next' or 'Finish' should be enabled or disabled.
    # Overriding this requires us to emit the 'completeChanges' signal where applicable.
    def isComplete(self) -> bool:
        return self._selected_device is not None

    def on_enter(self) -> None:
        button = self.wizard().button(QWizard.CustomButton1)
        button.setText(_("&Rescan"))
        button.setContentsMargins(10, 0, 10, 0)
        button.clicked.connect(self._on_rescan_clicked)

        self._alive = True
        if len(self._devices):
            self._display_scan_success_results()
        else:
            self._initiate_scan()

    def on_leave(self) -> None:
        self._show_rescan_button(False)
        self._alive = False
        self._scan_generation += 1
        button = self.wizard().button(QWizard.CustomButton1)
        button.clicked.disconnect()

    def _on_rescan_clicked(self, *checked) -> None:
        self._initiate_scan()

    def _display_scan_in_progress(self) -> None:
        progress_bar = QProgressBar()
        progress_bar.setRange(0, 0)
        progress_bar.setOrientation(Qt.Horizontal)
        progress_bar.setMinimumWidth(250)
        # This explicitly needs to be done for the progress bar otherwise it has some RHS space.
        progress_bar.setAlignment(Qt.AlignCenter)

        progress_label = QLabel(_("Please wait for hardware wallets to be located."))

        vbox = QVBoxLayout()
        vbox.addStretch(1)
        vbox.addWidget(progress_bar, alignment=Qt.AlignCenter)
        vbox.addWidget(progress_label, alignment=Qt.AlignCenter)
        vbox.addStretch(1)

        if self.layout():
            QWidget().setLayout(self.layout())

        hlayout = QHBoxLayout()
        hlayout.addStretch(1)
        hlayout.addLayout(vbox)
        hlayout.addStretch(1)
        self.setLayout(hlayout)

    def _display_scan_success_results(self) -> None:
        choices = []
        for name, info in self._devices:
            state = _("initialized") if info.initialized else _("wiped")
            label = info.label or _("An unnamed {}").format(name)
            choices.append(((name, info), f"{label} [{name}, {state}]"))

        c_values = [x[0] for x in choices]
        c_titles = [x[1] for x in choices]
        def _on_choice_clicked(choices: ChoicesLayout) -> None:
            self._selected_device = c_values[choices.selected_index()]
            self.completeChanged.emit()
        self._selected_device = c_values[0] if len(c_values) == 1 else None
        self.completeChanged.emit()

        message = _('Select a device')
        self._choices = ChoicesLayout(message, c_titles, on_clicked=_on_choice_clicked,
            checked_index=0 if len(c_values) == 1 else -1)

        vbox = QVBoxLayout()
        vbox.addLayout(self._choices.layout())
        if self._device_debug_message:
            details = QLabel(self._device_debug_message)
            details.setTextFormat(Qt.PlainText)
            details.setWordWrap(True)
            vbox.addWidget(details)
        vbox.addStretch(1)

        if self.layout():
            QWidget().setLayout(self.layout())
        self.setLayout(vbox)

    def _display_scan_failure_results(self) -> None:
        label = QLabel(NO_DEVICES_FOUND_TEXT + "\n")
        label.setWordWrap(True)

        logo_grid = QGridLayout()
        logo_grid.setSpacing(18)
        logo_grid.setColumnMinimumWidth(0, 70)
        logo_grid.setColumnStretch(1,1)

        logo = QLabel()
        logo.setAlignment(Qt.AlignCenter)
        lockfile = "icons8-usb-disconnected-80.png"
        logo.setPixmap(QPixmap(icon_path(lockfile)).scaledToWidth(80))

        logo_grid.addWidget(logo,  0, 0)
        logo_grid.addWidget(label, 0, 1, 1, 2)

        grid = QGridLayout()
        grid.setSpacing(8)
        grid.setColumnMinimumWidth(0, 150)
        grid.setColumnMinimumWidth(1, 100)
        grid.setColumnStretch(1,1)

        scan_text_label = QLabel(_("Debug messages") +":")
        scan_text_edit = QTextEdit()
        scan_text_edit.setText(self._device_debug_message)
        scan_text_edit.setReadOnly(True)
        grid.addWidget(scan_text_label, 0, 0, 2, 1, Qt.AlignRight)
        grid.addWidget(scan_text_edit, 1, 0, 2, 2, Qt.AlignLeft)

        vbox = QVBoxLayout()
        vbox.setContentsMargins(10, 10, 20, 10)
        vbox.addLayout(logo_grid)
        vbox.addSpacing(10)
        vbox.addWidget(scan_text_label)
        vbox.addWidget(scan_text_edit)
        vbox.addLayout(grid)
        retry = QPushButton(_('Try again'))
        retry.clicked.connect(self._initiate_scan)
        vbox.addWidget(retry)
        vbox.addStretch(1)

        if self.layout():
            QWidget().setLayout(self.layout())
        self.setLayout(vbox)

    def _show_rescan_button(self, is_shown: bool) -> None:
        if not self._alive:
            return
        button = self.wizard().button(QWizard.CustomButton1)
        button.setVisible(is_shown)

    def _initiate_scan(self) -> None:
        self._scan_generation += 1
        generation = self._scan_generation
        self._show_rescan_button(False)

        self._devices = []
        self._selected_device = None
        self._device_debug_message = None
        self.completeChanged.emit()

        self._display_scan_in_progress()

        app_state.app.run_in_thread(self._scan_attempt,
            on_done=lambda future: self._on_scan_complete(future, generation))

    def _on_scan_complete(self, future: concurrent.futures.Future, generation: int) -> None:
        if sip.isdeleted(self) or not self._alive or generation != self._scan_generation:
            return
        try:
            result = future.result()
            self._devices = list(result.devices)
            self._device_debug_message = '\n\n'.join(result.errors.values())
        except Exception as exc:
            self._devices = []
            self._device_debug_message = str(exc)
        if self._devices:
            self._display_scan_success_results()
        else:
            self._display_scan_failure_results()
        self._show_rescan_button(True)
        self.completeChanged.emit()

    def _scan_attempt(self):
        return app_state.device_manager.hardware.discover()


class SetupHardwareWalletAccountPage(QWizardPage):
    _plugin: Any
    _plugin_debug_message: Optional[str]
    _derivation_default: Sequence[int] = tuple(bip32_decompose_chain_string(
        bip44_derivation_cointype(0, 0)))
    _derivation_user: Optional[Sequence[int]] = None

    def __init__(self, wizard: AccountWizard):
        super().__init__(wizard)

        self._plugin = None
        self._plugin_debug_message = None

        self.setTitle(_("Import a Hardware Wallet as an Account"))
        self.setFinalPage(False)

    # Qt method called to get the Id of the next page.
    def nextId(self):
        return -1

    # Qt method called when 'Next' or 'Finish' is clicked for last-minute validation.
    def validatePage(self) -> bool:
        wizard: AccountWizard = self.wizard()
        if self._create_account(main_window=wizard._main_window):
            return True
        return False

    # Qt method called to determine if 'Next' or 'Finish' should be enabled or disabled.
    # Overriding this requires us to emit the 'completeChanges' signal where applicable.
    def isComplete(self) -> bool:
        return (self._plugin_debug_message is None and self._plugin is not None and
            self._derivation_user is not None)

    def on_enter(self) -> None:
        # if self.wallet_type=='multisig':
        #     # There is no general standard for HD multisig.
        #     # This is partially compatible with BIP45; assumes index=0
        #     default_derivation = "m/45'/0"

        self._initiate_setup()

    def on_leave(self) -> None:
        self._plugin = None
        self._plugin_debug_message = None

    def _initiate_setup(self) -> None:
        self._setup_device()
        if self._plugin_debug_message is None and self._plugin is not None:
            self._display_setup_success_results()
        else:
            self._display_setup_failure_results()
        self.completeChanged.emit()

    def _display_setup_success_results(self) -> None:
        wizard: AccountWizard = self.wizard()
        name, device_info = wizard.get_selected_device()

        text = DEVICE_SETUP_SUCCESS_TEXT.format(name.capitalize(),
            compose_chain_string(self._derivation_default))

        label = QLabel(text + "\n")
        label.setWordWrap(True)

        logo = QLabel()
        logo.setAlignment(Qt.AlignCenter)
        logo_filename = "icons8-usb-connected-80.png"
        logo.setPixmap(QPixmap(icon_path(logo_filename)).scaledToWidth(80))

        logo_grid = QGridLayout()
        logo_grid.setSpacing(18)
        logo_grid.setColumnMinimumWidth(0, 70)
        logo_grid.setColumnStretch(1,1)
        logo_grid.addWidget(logo,  0, 0, Qt.AlignTop)
        logo_grid.addWidget(label, 0, 1, 1, 2)

        grid = QGridLayout()
        grid.setSpacing(8)
        grid.setColumnMinimumWidth(0, 150)
        grid.setColumnMinimumWidth(1, 100)
        grid.setContentsMargins(50, 10, 50, 10)
        grid.setColumnStretch(1,1)

        path_text = compose_chain_string(self._derivation_default)
        self._path_edit = QLineEdit()
        self._path_edit.setText(path_text)
        self._path_edit.textEdited.connect(self._on_derivation_path_changed)
        self._path_edit.setFixedWidth(140)
        self._on_derivation_path_changed(path_text)

        grid.addWidget(QLabel(_("Derivation path")), 1, 0, 1, 1, Qt.AlignRight)
        grid.addWidget(self._path_edit, 1, 1, 1, 2, Qt.AlignLeft)

        vbox = QVBoxLayout()
        vbox.setContentsMargins(10, 10, 20, 10)
        vbox.addLayout(logo_grid)
        vbox.addSpacing(10)
        vbox.addLayout(grid)
        vbox.addStretch(1)

        if self.layout():
            QWidget().setLayout(self.layout())
        self.setLayout(vbox)

    def _display_setup_failure_results(self) -> None:
        # This might happen for instance, if the "bitcoin cash" application is not open.
        label = QLabel(DEVICE_SETUP_ERROR_TEXT + "\n")
        label.setWordWrap(True)

        logo_grid = QGridLayout()
        logo_grid.setSpacing(18)
        logo_grid.setColumnMinimumWidth(0, 70)
        logo_grid.setColumnStretch(1,1)

        logo = QLabel()
        logo.setAlignment(Qt.AlignCenter)
        logo_filename = "icons8-usb-disconnected-80.png"
        logo.setPixmap(QPixmap(icon_path(logo_filename)).scaledToWidth(80))

        logo_grid.addWidget(logo,  0, 0)
        logo_grid.addWidget(label, 0, 1, 1, 2)

        grid = QGridLayout()
        grid.setSpacing(8)
        grid.setColumnMinimumWidth(0, 150)
        grid.setColumnMinimumWidth(1, 100)
        grid.setColumnStretch(1,1)

        scan_text_label = QLabel(_("Debug messages:"))
        scan_text_edit = QTextEdit()
        scan_text_edit.setText(self._plugin_debug_message)
        scan_text_edit.setReadOnly(True)
        grid.addWidget(scan_text_label, 0, 0, 2, 1, Qt.AlignRight)
        grid.addWidget(scan_text_edit, 1, 0, 2, 2, Qt.AlignLeft)

        vbox = QVBoxLayout()
        vbox.setContentsMargins(10, 10, 20, 10)
        vbox.addLayout(logo_grid)
        vbox.addSpacing(10)
        vbox.addWidget(scan_text_label)
        vbox.addWidget(scan_text_edit)
        vbox.addLayout(grid)
        retry = QPushButton(_('Try again'))
        retry.clicked.connect(self._initiate_setup)
        vbox.addWidget(retry)
        vbox.addStretch(1)

        if self.layout():
            QWidget().setLayout(self.layout())
        self.setLayout(vbox)

    def _setup_device(self) -> None:
        self._plugin = None
        self._handler = None
        self._plugin_debug_message = None
        wizard = self.wizard()
        name, info = wizard.get_selected_device()
        support = app_state.device_manager.hardware
        try:
            plugin = support.plugin(name)
            # QObject signals belong to the GUI thread, USB operations to the worker.
            handler = plugin.create_handler(wizard)
            run_device_operation(wizard, lambda: support.connect(name, info, handler))
            self._handler = handler
            self._plugin = plugin
        except UserCancelled:
            self._plugin_debug_message = _('Device setup was cancelled. Go back to try again.')
        except Exception as exc:
            self._plugin_debug_message = str(exc)
            app_state.device_manager.unpair_id(info.device.id_)

    def _on_derivation_path_changed(self, text: str) -> None:
        path_text = self._path_edit.text().strip()
        try:
            self._derivation_user = tuple(bip32_decompose_chain_string(path_text)) or None
        except ValueError:
            self._derivation_user = None
        self.completeChanged.emit()


    @protected
    def _create_account(self, main_window: Optional[ElectrumWindow]=None,
            password: Optional[str]=None) -> bool:
        # The derivation path is valid, proceed to create the account.
        wizard: AccountWizard = self.wizard()
        name, device_info = wizard.get_selected_device()

        derivation_text = compose_chain_string(self._derivation_user)
        try:
            mpk = run_device_operation(wizard, lambda:
                app_state.device_manager.hardware.public_key(name, device_info, derivation_text,
                    self._handler))
        except Exception as e:
            logger.exception("Failed getting master public key for hardware wallet (%s, %s)",
                self._derivation_user, derivation_text)
            MessageBox.show_error(str(e))
            return False

        label = device_info.label
        data = {
            'hw_type': name,
            'derivation': derivation_text,
            'xpub': mpk.to_extended_key_string(),
            'label': '{} · {}'.format(label.strip() if label and label.strip() else name,
                derivation_text),
        }
        keystore = instantiate_keystore(DerivationType.HARDWARE, data)
        from .account_wizard import ResultType
        return wizard.set_keystore_result(ResultType.HARDWARE, keystore)
