# The Open BSV license.
#
# Copyright © 2020 Bitcoin Association
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the “Software”), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
#   1. The above copyright notice and this permission notice shall be included
#      in all copies or substantial portions of the Software.
#   2. The Software, and any software that is derived from the Software or parts
#      thereof, can only be used on the Bitcoin SV blockchains. The Bitcoin SV
#      blockchains are defined, for purposes of this license, as the Bitcoin
#      blockchain containing block height #556767 with the hash
#      “000000000000000001d956714215d96ffc00e0afda4cd0a96c96f8d802b1662b” and
#      the test blockchains that are supported by the unmodified Software.
#
# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import concurrent
import enum
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union

from bitcoinx import (Address, Base58Error, bip32_decompose_chain_string,
    bip32_key_from_string, PrivateKey, P2SH_Address)

from PyQt5.QtCore import QObject, QSize, Qt
from PyQt5.QtGui import QBrush, QColor, QPainter, QPalette, QPen, QPixmap, QTextOption
from PyQt5.QtWidgets import (
    QCheckBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QProgressBar, QRadioButton, QSizePolicy, QSlider, QTextEdit,
    QVBoxLayout, QWidget, QWizard, QWizardPage, QPushButton
)

from electrumsv.app_state import app_state
from electrumsv.bitcoin import compose_chain_string, is_new_seed, is_old_seed
from electrumsv.constants import (DEFAULT_COSIGNER_COUNT, DerivationType, IntFlag,
    KeystoreTextType, MAXIMUM_COSIGNER_COUNT, ScriptType)
from electrumsv.device import DeviceInfo
from electrumsv.exceptions import UserCancelled
from electrumsv.i18n import _
from electrumsv.keystore import (bip39_is_checksum_valid, bip44_derivation_cointype, from_seed,
    instantiate_keystore_from_text, KeyStore, Multisig_KeyStore)
from electrumsv.logs import logs
from electrumsv.networks import Net
from electrumsv.storage import WalletStorage
from electrumsv.wallet import Wallet, instantiate_keystore

from .cosigners_view import CosignerState, CosignerList
from .main_window import ElectrumWindow
from .util import (ChoicesLayout, icon_path, MessageBox, MessageBoxMixin, protected, query_choice,
    read_QIcon)
from .wizard_common import BaseWizard, DEFAULT_WIZARD_FLAGS, WizardFlags, WizardFormSection
from .theme import account_types_style, Theme, theme_from_config
from .qrtextedit import ScanQRTextEdit


logger = logs.get_logger('wizard-account')

DeviceList = List[Tuple[str, DeviceInfo]]

PASSWORD_EXISTING_TEXT = _("Your wallet has a password, and you will need to provide that "
    "password in order to secure this account.")

KeystoreMatchType = Union[str, Set[str]]


class AccountPage(enum.IntEnum):
    NONE = 0

    ADD_ACCOUNT_MENU = 100

    CREATE_MULTISIG_ACCOUNT = 300
    CREATE_MULTISIG_ACCOUNT_CUSTOM = 301
    CREATE_MULTISIG_ACCOUNT_COSIGNERS = 302

    IMPORT_ACCOUNT_FILE = 400
    IMPORT_ACCOUNT_TEXT = 405
    IMPORT_ACCOUNT_TEXT_CUSTOM = 410
    WATCH_ONLY_ACCOUNT = 415
    
    FIND_HARDWARE_WALLET = 500
    SETUP_HARDWARE_WALLET = 505

class KeyFlags(enum.IntEnum):
    NONE = 0
    CAN_BE_MULTISIG_READ_ONLY = 1 << 0
    CAN_BE_MULTISIG_WRITABLE = (1 << 1) | CAN_BE_MULTISIG_READ_ONLY

TextKeystoreTypeFlags = {
    KeystoreTextType.ADDRESSES: KeyFlags.NONE,
    KeystoreTextType.PRIVATE_KEYS: KeyFlags.NONE,
    KeystoreTextType.EXTENDED_PUBLIC_KEY: KeyFlags.CAN_BE_MULTISIG_READ_ONLY,
    KeystoreTextType.EXTENDED_PRIVATE_KEY: KeyFlags.CAN_BE_MULTISIG_WRITABLE,
    KeystoreTextType.BIP39_SEED_WORDS: KeyFlags.CAN_BE_MULTISIG_WRITABLE,
    KeystoreTextType.ELECTRUM_SEED_WORDS: KeyFlags.CAN_BE_MULTISIG_WRITABLE,
    KeystoreTextType.ELECTRUM_OLD_SEED_WORDS: KeyFlags.CAN_BE_MULTISIG_WRITABLE,
}

class ResultType(IntFlag):
    UNKNOWN = 0

    NEW = 1
    MULTISIG = 2
    IMPORTED = 3
    HARDWARE = 4


def request_password(parent: Optional[QWidget], storage: WalletStorage) -> Optional[str]:
    from .password_dialog import PasswordDialog
    d = PasswordDialog(parent, PASSWORD_EXISTING_TEXT, password_check_fn=storage.is_password_valid)
    d.setMaximumWidth(200)
    return d.run()



class AccountWizard(BaseWizard, MessageBoxMixin):
    HELP_DIRNAME = "account-wizard"

    _last_page_id: Optional[AccountPage] = None
    _selected_device: Optional[Tuple[str, DeviceInfo]] = None
    _keystore: Optional[KeyStore] = None
    _keystore_type = ResultType.UNKNOWN

    def __init__(self, main_window: ElectrumWindow,
            flags: WizardFlags=DEFAULT_WIZARD_FLAGS, parent: Optional[QWidget]=None) -> None:
        if parent is None:
            parent = main_window
        super().__init__(parent)

        theme = theme_from_config(app_state.config, True)

        if theme in (Theme.DARK, Theme.PINK, Theme.ORANGE):
            self.setWizardStyle(QWizard.ClassicStyle)

        self.flags = flags

        self._main_window = main_window

        self._wallet: Wallet = main_window._wallet

        self._text_import_type: Optional[KeystoreTextType] = None
        self._text_import_matches: Optional[KeystoreMatchType] = None

        self.set_subtitle("")
        self.setModal(True)
        self.setMinimumSize(600, 600)
        self.setOption(QWizard.HaveCustomButton1, True)
        self.button(QWizard.CustomButton1).setVisible(False)

        self.setPage(AccountPage.ADD_ACCOUNT_MENU, AddAccountWizardPage(self))
        self.setPage(AccountPage.IMPORT_ACCOUNT_TEXT, ImportWalletTextPage(self))
        self.setPage(AccountPage.IMPORT_ACCOUNT_TEXT_CUSTOM, ImportWalletTextCustomPage(self))
        self.setPage(AccountPage.WATCH_ONLY_ACCOUNT, WatchOnlyAccountPage(self))
        self.setPage(AccountPage.CREATE_MULTISIG_ACCOUNT, CreateMultisigAccountPage(self))
        self.setPage(AccountPage.CREATE_MULTISIG_ACCOUNT_CUSTOM,
            CreateMultisigAccountCustomPage(self))
        self.setPage(AccountPage.CREATE_MULTISIG_ACCOUNT_COSIGNERS,
            MultisigAccountCosignerListPage(self))
        from .hardware_accounts import FindHardwareWalletAccountPage, SetupHardwareWalletAccountPage
        self.setPage(AccountPage.FIND_HARDWARE_WALLET, FindHardwareWalletAccountPage(self))
        self.setPage(AccountPage.SETUP_HARDWARE_WALLET, SetupHardwareWalletAccountPage(self))

        self.setStartId(AccountPage.ADD_ACCOUNT_MENU)

    # Used by hardware wallets.
    def query_choice(self, msg: str, choices: Iterable[str]) -> Optional[int]:
        return query_choice(self, msg, choices)

    def set_subtitle(self, subtitle: str) -> None:
        suffix = f" - {subtitle}" if len(subtitle) else ""
        self.setWindowTitle(f'ElectrumSV{suffix}')

    def set_selected_device(self, device: Optional[Tuple[str, DeviceInfo]]) -> None:
        self._selected_device = device

    def get_selected_device(self) -> Optional[Tuple[str, DeviceInfo]]:
        return self._selected_device

    def get_main_window(self) -> ElectrumWindow:
        "For page access to the parent window."
        return self._main_window

    def get_wallet(self) -> Wallet:
        "For page access to any wallet."
        return self._wallet

    def set_text_import_matches(self, text_type: KeystoreTextType,
            text_matches: KeystoreMatchType) -> None:
        self._text_import_type = text_type
        self._text_import_matches = text_matches

    def get_text_import_type(self) -> Optional[KeystoreTextType]:
        return self._text_import_type

    def get_text_import_matches(self) -> Optional[KeystoreMatchType]:
        return self._text_import_matches

    def has_result(self) -> bool:
        return self._keystore_type != ResultType.UNKNOWN

    def get_keystore(self) -> KeyStore:
        return self._keystore

    def set_keystore_result(self, result_type: ResultType, keystore: Optional[KeyStore]) -> bool:
        self._keystore_type = result_type
        self._keystore = keystore
        if keystore is None:
            return False

        # For now, all other result types are expected to be collected by the invoking logic of
        # this account wizard instance.
        if self.flags & WizardFlags.ACCOUNT_RESULT:
            account = self._wallet.create_account_from_keystore(keystore)
            return account is not None

        return True

    def set_text_entry_account_result(self, result_type: ResultType, text_type: KeystoreTextType,
            script_type: ScriptType, text_matches: KeystoreMatchType,
            password: Optional[str]) -> bool:
        self._keystore_type = result_type

        if self.flags & WizardFlags.ACCOUNT_RESULT:
            assert password is not None
            account = self._wallet.create_account_from_text_entries(
                text_type, script_type, text_matches, password)
            return account is not None
        else:
            raise NotImplementedError("Invalid attempt to generate keyless keystore data")


class AddAccountWizardPage(QWizardPage):
    def __init__(self, wizard: AccountWizard) -> None:
        super().__init__(wizard)

        self.setTitle(_("Account Types"))
        self.setFinalPage(False)

        page = self

        # Custom QListWidget to handle Enter key
        class ListWidget(QListWidget):
            def keyPressEvent(self, event):
                key = event.key()
                if key == Qt.Key_Return or key == Qt.Key_Enter:
                    page._event_key_press_selection()
                else:
                    super(ListWidget, self).keyPressEvent(event)

        option_list = self._option_list = ListWidget()
        option_list.setIconSize(QSize(30, 30))

        theme = theme_from_config(app_state.config)
        option_list.setStyleSheet(
            account_types_style(theme)
        )


        # Populate the list
        for entry in self._get_entries():
            if not entry.get("enabled", True):
                continue
            if wizard.flags & entry.get("mode_mask", WizardFlags.NONE) == WizardFlags.NONE:
                continue
            list_item = QListWidgetItem()
            list_item.setSizeHint(QSize(40, 40))
            list_item.setIcon(read_QIcon(entry['icon_filename']))
            list_item.setText(entry['description'])
            list_item.setData(Qt.UserRole, entry)
            option_list.addItem(list_item)

        option_list.setMaximumWidth(400)
        option_list.setWordWrap(True)

        option_detail = self._option_detail = QLabel()
        option_detail.setMinimumWidth(200)
        option_detail.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        option_detail.setTextFormat(Qt.RichText)
        option_detail.setWordWrap(True)
        option_detail.setOpenExternalLinks(True)
        option_detail.setText(self._get_entry_detail())

        # Informational message about BIP39 enforcement
        info_label = QLabel(
            _("ElectrumSV now generates BIP39 seeds by default. "
              "Older Electrum-style seeds can still be imported.")
        )
        info_label.setWordWrap(True)
        
        if theme_from_config(app_state.config, True) == Theme.DARK:
            info_label.setStyleSheet(
                "color: #b8bdc4; background-color: transparent; font-style: italic;"
            )
        else:
            info_label.setStyleSheet(
                "color: gray; background-color: transparent; font-style: italic;"
            )

        info_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)

        # Horizontal layout for list + detail
        layout = QHBoxLayout()
        layout.setSpacing(15)
        layout.addWidget(option_list)
        layout.addWidget(option_detail)

        # Outer vertical layout to include info label below list/detail
        outer_layout = QVBoxLayout()
        outer_layout.addLayout(layout)
        outer_layout.addWidget(info_label)
        self.setLayout(outer_layout)

        # Connect signals
        option_list.itemSelectionChanged.connect(self._event_selection_changed)
        option_list.itemDoubleClicked.connect(self._event_double_click_item)


    # Qt default QWizardPage event when page is entered.
    def on_enter(self) -> None:
        wizard: AccountWizard = self.wizard()
        # Clear the result. This shouldn't be needed except in the case of an unexpected error
        # where the wizard does not exit and the user returns back to this page.
        wizard.set_keystore_result(ResultType.UNKNOWN, None)
        # The click event arrives after the standard wizard next page handling. We use it to
        # perform actions that finish on the current page.
        next_button = wizard.button(QWizard.NextButton)
        next_button.clicked.connect(self._event_click_next_button)
        self._restore_button_text = wizard.buttonText(QWizard.NextButton)

    def on_leave(self) -> None:
        wizard: AccountWizard = self.wizard()
        next_button = wizard.button(QWizard.NextButton)
        next_button.clicked.disconnect(self._event_click_next_button)
        wizard.setButtonText(QWizard.NextButton, self._restore_button_text)

    # Qt method called to determine if 'Next' or 'Finish' should be enabled or disabled.
    # Overriding this requires us to emit the 'completeChanges' signal where applicable.
    def isComplete(self) -> bool:
        return len(self._option_list.selectedItems()) > 0

    # Qt method called when 'Next' or 'Finish' is clicked for last-minute validation.
    def validatePage(self) -> bool:
        if self.isComplete():
            next_page_id = self.nextId()
            if next_page_id != AccountPage.NONE:
                return True
            # In the case we manually accept for a no-page option, check we have the result.
            wizard: AccountWizard = self.wizard()
            if wizard.has_result():
                return True
        return False

    # Qt method called to get the Id of the next page.
    def nextId(self) -> int:
        items = self._option_list.selectedItems()
        if len(items) > 0:
            return items[0].data(Qt.UserRole).get("page", AccountPage.NONE)
        return AccountPage.NONE

    def _event_selection_changed(self) -> None:
        wizard: AccountWizard = self.wizard()
        items = self._option_list.selectedItems()
        if len(items) > 0:
            entry = items[0].data(Qt.UserRole)
            button_text = entry.get("button_text", self._restore_button_text)
            self._option_detail.setText(self._get_entry_detail(entry))
        else:
            button_text = self._restore_button_text
            self._option_detail.setText(self._get_entry_detail())
        wizard.setButtonText(QWizard.NextButton, button_text)
        self.completeChanged.emit()

    def _event_key_press_selection(self) -> None:
        items = self._option_list.selectedItems()
        if len(items):
            self._select_item(items[0])

    def _event_double_click_item(self, item: QListWidgetItem) -> None:
        self._option_list.setCurrentItem(item)
        self._select_item(item)

    def _event_click_next_button(self) -> None:
        items = self._option_list.selectedItems()
        if len(items):
            self._select_item(items[0], direct_only=True)

    def _select_item(self, item: QListWidgetItem, direct_only: bool=False) -> None:
        entry = item.data(Qt.UserRole)
        page = entry.get("page", AccountPage.NONE)
        if page == AccountPage.NONE:
            # This is something that either finishes on this page, or requires custom handling
            # before manually invoking a move to the next page.
            entry['handler']()
        elif not direct_only:
            self.completeChanged.emit()
            self.wizard().next()

    def _create_new_account(self) -> None:
        wizard: AccountWizard = self.wizard()
        wallet_storage = wizard.get_main_window()._wallet.get_storage()
        password = request_password(self, wallet_storage)
        if password is None:
            return

        # Always generate a secure BIP39 mnemonic (12 words)
        from electrumsv.mnemonic import generate_bip39_seed
        seed_phrase = generate_bip39_seed(12)
        bip39_passphrase = ''  # user passphrase for BIP39; empty by default

        # Prefill standard BIP44 derivation path for account 0
        from electrumsv.keystore import from_bip39_seed, bip44_derivation_cointype, Net
        derivation = bip44_derivation_cointype(Net.BIP44_COIN_TYPE, 0)
        keystore = from_bip39_seed(seed_phrase, bip39_passphrase, derivation)

        # Encrypt/update keystore password
        keystore.update_password(password)

        # Return result to wizard
        wizard.set_keystore_result(ResultType.NEW, keystore)
        wizard.accept()


    def _get_entry_detail(self, entry=None):
        title_start_html = "<b>"
        title_end_html = "</b>"
        if entry is None:
            title_start_html = title_end_html = ""
            entry = {
                'icon_filename': 'icons8-decision-80.png',
                'description': _("Select the way in which you want to add a new account "+
                    "from the options to the left."),
            }
        html = f"""
        <center>
            <img src='{icon_path(entry['icon_filename'])}' width=80 height=80 />
        </center>
        <p>
            {title_start_html}
            {entry['description']}
            {title_end_html}
        </p>
        """
        html += entry.get('long_description', '')
        return html

    def _get_entries(self):
        seed_phrase_html = ("<p>"+
            _("A seed phrase is a way of storing an account's private key. "+
              "Using it ElectrumSV can access the wallet's previous "+
              "payments, and send and receive the coins in the wallet.") +
            "</p>")

        original_wallet_html = ("<p>"+
            _("If the original wallet application a seed phrase came from is still being used to "+
              "access the given account, then it is not safe to access it in ElectrumSV "+
              "while this is the case.") +
            " %(extra)s"+
            "</p>")

        original_wallet_unsafe_html = original_wallet_html % {
            "extra": _("%(wallet_name)s is one of these applications. If you are still using it, "+
                       "it may get confused if you do more than watch, like sending and "+
                       "receiving coins using ElectrumSV."),
        }

        original_wallet_safe_html = original_wallet_html % {
            "extra": _("%(wallet_name)s however, works in a compatible way "+
                       "where it should be possible to use both it and "+
                       "ElectrumSV at the same time."),
        }

        return [
            {
                'page': AccountPage.NONE,
                'description': _("Standard"),
                'icon_filename': 'icons8-create-80.png',
                'long_description': _("If you want to create a brand new standard account in "
                    "ElectrumSV this is the option you want.") +"<br/><br/>"+ _("A "
                    "standard account is one where you are in control of all payments."),
                'button_text': _("Create"),
                'enabled': True,
                'mode_mask': WizardFlags.ALL_MODES,
                'handler': self._create_new_account,
            },
            {
                'page': AccountPage.CREATE_MULTISIG_ACCOUNT,
                'description': _("Multi-signature"),
                'icon_filename': 'icons8-group-task-80-blueui-active.png',
                'long_description': _("If you want to create a brand new multi-signature account "
                    "in ElectrumSV this is the option you want.") +"<br/><br/>"+ _("A "
                    "multi-signature account is one where more than one person is required to "
                    "approve each payment. This requires that the participants, or cosigners, "
                    "coordinate the signing of each payment."),
                'enabled': True,
                'mode_mask': WizardFlags.STANDARD_MODE,
            },

            {
                'page': AccountPage.WATCH_ONLY_ACCOUNT,
                'description': _("Watch-only"),
                'icon_filename': 'icons8-lock-80.png',
                'long_description': _("Monitor an existing wallet without importing its "
                    "private keys.") +"<br/><br/>"+ _("You will need the wallet's "
                    "extended public key. A watch-only account can view addresses and "
                    "transactions, but cannot spend coins."),
                'enabled': True,
                'mode_mask': WizardFlags.ALL_MODES,
            },

            {
                'page': AccountPage.IMPORT_ACCOUNT_FILE,
                'description': _("Import from file"),
                'icon_filename': 'icons8-document.svg',
                'long_description': _("..."),
                'enabled': False,
            },
            {
                'page': AccountPage.IMPORT_ACCOUNT_TEXT,
                'description': _("Import from text (any seed phrase, public keys, "+
                                 "private keys or addresses)"),
                'icon_filename': 'icons8-brain-80.png',
                'long_description': _("If you have some text to paste, or type in, and want "
                    "ElectrumSV to examine it and offer you some choices on how it can be "
                    "imported, this is the option you probably want."),
                'enabled': True,
                'mode_mask': WizardFlags.ALL_MODES,
            },
            {
                'page': AccountPage.FIND_HARDWARE_WALLET,
                'description': _('Connect hardware wallet'),
                'long_description': _('Use an initialized Ledger, Trezor, KeepKey or BitBox01. '
                    'Keep the recovery seed on your device. Ledger requires the Bitcoin Cash app. '
                    'These adapters are experimental; check the tested-version list before use.'),
                'icon_filename': 'icons8-usb-2-80-blueui-active.png',
                'enabled': True,
                'mode_mask': WizardFlags.ALL_MODES,
            },
        ]



class WatchOnlyAccountPage(QWizardPage):
    def __init__(self, wizard: AccountWizard) -> None:
        super().__init__(wizard)

        self.setTitle(_("Watch-only account"))
        self.setFinalPage(True)

        self._label = QLabel(
            _("Enter the wallet's extended public key. "
              "This allows ElectrumSV to monitor the wallet without "
              "having access to its private keys.")
        )
        self._label.setWordWrap(True)

        self._key_edit = QLineEdit()
        self._key_edit.setPlaceholderText(_("Extended public key"))
        self._key_edit.textChanged.connect(self._on_key_changed)
        self._qr_button = QPushButton(_("Scan QR"))
        self._qr_button.clicked.connect(self._on_scan_qr)

        self._status_label = QLabel()
        self._status_label.setWordWrap(True)

        layout = QVBoxLayout()
        layout.addWidget(self._label)
        layout.addSpacing(10)
        layout.addWidget(self._key_edit)
        layout.addWidget(self._qr_button)
        layout.addWidget(self._status_label)
        layout.addStretch(1)
        self.setLayout(layout)


    def _on_scan_qr(self) -> None:
        scanner = ScanQRTextEdit()
        data = scanner.qr_input()

        if data:
            self._key_edit.setText(data.strip())


    def _on_key_changed(self, text: str) -> None:
        text = text.strip()

        if not text:
            self._status_label.setText("")
            self.completeChanged.emit()
            return

        try:
            key = bip32_key_from_string(text)
        except (Base58Error, ValueError):
            self._status_label.setText(_("Invalid extended public key."))
        else:
            if isinstance(key, PrivateKey):
                self._status_label.setText(
                    _("Please enter an extended public key, not a private key.")
                )
            else:
                self._status_label.setText(
                    _("Valid extended public key.")
                )

        self.completeChanged.emit()

    def isComplete(self) -> bool:
        text = self._key_edit.text().strip()

        if not text:
            return False

        try:
            key = bip32_key_from_string(text)
        except (Base58Error, ValueError):
            return False

        return not isinstance(key, PrivateKey)

    def validatePage(self) -> bool:
        text = self._key_edit.text().strip()

        try:
            key = bip32_key_from_string(text)
        except (Base58Error, ValueError):
            return False

        if isinstance(key, PrivateKey):
            return False

        keystore = instantiate_keystore_from_text(
            KeystoreTextType.EXTENDED_PUBLIC_KEY,
            text,
            None,
        )

        wizard: AccountWizard = self.wizard()
        return wizard.set_keystore_result(ResultType.IMPORTED, keystore)

    def nextId(self) -> int:
        return -1

    def on_enter(self) -> None:
        self._key_edit.clear()
        self._status_label.clear()
        self.completeChanged.emit()

    def on_leave(self) -> None:
        pass





class ImportWalletTextPage(QWizardPage):
    def __init__(self, wizard: AccountWizard) -> None:
        super().__init__(wizard)

        self.setTitle(_("Import Account From Text"))
        self.setFinalPage(True)

        self._next_page_id = -1
        self._checked_match_type: Optional[KeystoreTextType] = None
        self._matches: Dict[KeystoreTextType, KeystoreMatchType] = {}

        self.text_area = QTextEdit()
        self.text_area.textChanged.connect(self._on_text_changed)
        self.text_area.setAcceptRichText(False)
        self.text_area.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.text_area.setTabChangesFocus(True)

        self._qr_button = QPushButton(_("Scan QR"))
        self._qr_button.clicked.connect(self._on_scan_qr)

        self._label = QLabel(_("Please enter some text and any valid matches will be "
            "made available below.."))

        hbox = QHBoxLayout()
        hbox.addStretch(1)
        hbox.addWidget(self._label)
        hbox.addStretch(1)

        self._addresses_button = QRadioButton(_("Addresses"))
        self._privkeys_button = QRadioButton(_("Private keys"))
        self._xpub_button = QRadioButton(_("Extended public key"))
        self._xprv_button = QRadioButton(_("Extended private key"))
        self._bip39seed_button = QRadioButton(_("BIP39 seed words"))
        self._esvseed_button = QRadioButton(_("Electrum seed words"))
        self._esvoldseed_button = QRadioButton(_("Electrum old-style seed words"))

        for match_type, button in self._get_buttons(wizard):
            def make_check_callback(match_type: KeystoreTextType) -> Callable[[bool], None]:
                def on_button_check(checked: bool=False):
                    self._on_match_type_selected(match_type)
                return on_button_check
            button.clicked.connect(make_check_callback(match_type))

        # Simple logic for two columns of options.
        textbuttons_box = QGridLayout()
        button_entries = self._get_buttons(wizard)
        row_length = len(button_entries) // 2
        if len(button_entries) % 2 != 0:
            row_length += 1
        for button_index in range(len(button_entries)):
            button = button_entries[button_index][1]
            row_index = button_index % row_length
            column_index = button_index // row_length
            textbuttons_box.addWidget(button, row_index, column_index)
        texttype_box = QGroupBox()
        texttype_box.setFlat(True)
        texttype_box.setLayout(textbuttons_box)

        hbox2 = QHBoxLayout()
        hbox2.addStretch(1)
        hbox2.addWidget(texttype_box)
        hbox2.addStretch(1)

        layout = QVBoxLayout()
        layout.addWidget(self.text_area)
        layout.addWidget(self._qr_button)
        layout.addLayout(hbox)
        layout.addLayout(hbox2)

        self.setLayout(layout)

    def _get_buttons(self, wizard: AccountWizard) -> List[Tuple[KeystoreTextType, QRadioButton]]:
        button_entries = [
            (KeystoreTextType.ADDRESSES, self._addresses_button),
            (KeystoreTextType.PRIVATE_KEYS, self._privkeys_button),
            (KeystoreTextType.EXTENDED_PUBLIC_KEY, self._xpub_button),
            (KeystoreTextType.EXTENDED_PRIVATE_KEY, self._xprv_button),
            (KeystoreTextType.BIP39_SEED_WORDS, self._bip39seed_button),
            (KeystoreTextType.ELECTRUM_SEED_WORDS, self._esvseed_button),
            (KeystoreTextType.ELECTRUM_OLD_SEED_WORDS, self._esvoldseed_button),
        ]
        if wizard.flags & WizardFlags.MULTISIG_MODE == WizardFlags.MULTISIG_MODE:
            entries = []
            for text_type, button in button_entries:
                key_flags = TextKeystoreTypeFlags[text_type]
                if key_flags == KeyFlags.NONE:
                    continue
                entries.append((text_type, button))
            return entries
        return button_entries

    def _set_matches(self, matches: Dict[KeystoreTextType, KeystoreMatchType]) -> None:
        self._checked_match_type = None
        self._matches = matches

        # These two types are expected to be the sole kind of match, but will have one or more
        # matches of that type.
        if KeystoreTextType.ADDRESSES in matches or KeystoreTextType.PRIVATE_KEYS in matches:
            if len(matches) > 1:
                matches.clear()

        wizard: AccountWizard = self.wizard()
        for match_type, button in self._get_buttons(wizard):
            if len(matches) == 1 and match_type in matches:
                self._checked_match_type = match_type
                button.setChecked(True)
            else:
                button.setChecked(False)
            button.setEnabled(match_type in matches)

        self._on_match_type_selected(self._checked_match_type)

    def _on_match_type_selected(self, match_type: Optional[KeystoreTextType]) -> None:
        self._checked_match_type = match_type

        button = self.wizard().button(QWizard.CustomButton1)
        button.setEnabled(self._checked_match_type is not None and \
            self._checked_match_type not in { KeystoreTextType.ADDRESSES,
                KeystoreTextType.PRIVATE_KEYS, KeystoreTextType.ELECTRUM_OLD_SEED_WORDS })
        self.completeChanged.emit()

    def _on_scan_qr(self) -> None:
        scanner = ScanQRTextEdit()
        data = scanner.qr_input()

        if data:
            self.text_area.setPlainText(data.strip())


    def _on_text_changed(self) -> None:
        matches: Dict[KeystoreTextType, KeystoreMatchType] = {}
        text = self.text_area.toPlainText().strip()

        # First try the matches that match the entire text.
        if is_old_seed(text):
            matches[KeystoreTextType.ELECTRUM_OLD_SEED_WORDS] = text
        if is_new_seed(text):
            matches[KeystoreTextType.ELECTRUM_SEED_WORDS] = text
        is_checksum_valid, is_wordlist_valid = bip39_is_checksum_valid(text)
        if is_checksum_valid and is_wordlist_valid:
            matches[KeystoreTextType.BIP39_SEED_WORDS] = text

        try:
            key = bip32_key_from_string(text)
            if isinstance(key, PrivateKey):
                matches[KeystoreTextType.EXTENDED_PRIVATE_KEY] = text
            else:
                matches[KeystoreTextType.EXTENDED_PUBLIC_KEY] = text
        except (Base58Error, ValueError):
            pass

        # If no full matches, try and match each "word".
        if not len(matches):
            text = text.split()
            for word in text:
                match_found = False
                try:
                    PrivateKey.from_text(word)
                except (Base58Error, ValueError):
                    pass
                else:
                    match_found = True
                    if KeystoreTextType.PRIVATE_KEYS not in matches:
                        matches[KeystoreTextType.PRIVATE_KEYS] = set()
                    matches[KeystoreTextType.PRIVATE_KEYS].add(word)

                try:
                    address = Address.from_string(word, Net.COIN)
                    if isinstance(address, P2SH_Address):
                        raise ValueError("P2SH not supported")
                except (Base58Error, ValueError):
                    pass
                else:
                    match_found = True
                    if KeystoreTextType.ADDRESSES not in matches:
                        matches[KeystoreTextType.ADDRESSES] = set()
                    matches[KeystoreTextType.ADDRESSES].add(word)

                if not match_found:
                    if KeystoreTextType.UNRECOGNIZED not in matches:
                        matches[KeystoreTextType.UNRECOGNIZED] = set()
                    matches[KeystoreTextType.UNRECOGNIZED].add(word)

        self._set_matches(matches)

    def _on_customize_button_clicked(self, *checked) -> None:
        assert self.isComplete()
        self._next_page_id = AccountPage.IMPORT_ACCOUNT_TEXT_CUSTOM

        wizard: AccountWizard = self.wizard()
        wizard.next()

    # Qt method called to determine if 'Next' or 'Finish' should be enabled or disabled.
    # Overriding this requires us to emit the 'completeChanges' signal where applicable.
    def isComplete(self) -> bool:
        return self._checked_match_type is not None

    # Qt method called when 'Next' or 'Finish' is clicked for last-minute validation.
    def validatePage(self) -> bool:
        # Called when 'Next' or 'Finish' is clicked for last-minute validation.
        assert self.isComplete()

        wizard: AccountWizard = self.wizard()
        if self._next_page_id == -1:
            # Create the account with no customisation
            if not self._create_account(main_window=wizard._main_window):
                return False
        else:
            wizard.set_text_import_matches(self._checked_match_type,
                self._matches[self._checked_match_type])
        return True

    # Qt method called to get the Id of the next page.
    def nextId(self) -> int:
        # Need to know if customize is clicked and go to the custom page.
        return self._next_page_id

    def on_enter(self) -> None:
        self._next_page_id = -1
        wizard: AccountWizard = self.wizard()

        button = wizard.button(QWizard.CustomButton1)
        button.setText(_("&Customize"))
        button.setContentsMargins(10, 0, 10, 0)
        button.clicked.connect(self._on_customize_button_clicked)
        button.setVisible(True)
        button.setEnabled(False)

        self._set_matches(self._matches)

    def on_leave(self) -> None:
        button = self.wizard().button(QWizard.CustomButton1)
        button.clicked.disconnect()
        button.setVisible(False)
        self._next_page_id = -1

    @protected
    def _create_account(self, main_window: Optional[ElectrumWindow]=None,
            password: Optional[str]=None) -> bool:
        wizard: AccountWizard = self.wizard()
        entries = self._matches[self._checked_match_type]
        if self._checked_match_type in (KeystoreTextType.ADDRESSES, KeystoreTextType.PRIVATE_KEYS):
            script_type = (ScriptType.P2PKH
                if self._checked_match_type == KeystoreTextType.PRIVATE_KEYS else ScriptType.NONE)
            if not wizard.set_text_entry_account_result(
                    ResultType.IMPORTED, self._checked_match_type,
                    script_type, entries, password):
                if main_window is not None:
                    main_window.show_error(
                        _("Nothing was imported because all of the keys or addresses are "
                          "already present in this wallet."))
                return False
        else:
            _keystore = instantiate_keystore_from_text(
                self._checked_match_type,
                self._matches[self._checked_match_type],
                password)

            if not wizard.set_keystore_result(ResultType.IMPORTED, _keystore):
                if main_window is not None:
                    main_window.show_error(
                        _("This account is already present in this wallet."))
                return False

        return True

class ImportWalletTextCustomPage(QWizardPage):
    def __init__(self, wizard: AccountWizard) -> None:
        super().__init__(wizard)

        self.setTitle(_("Import Account From Text With Customizations"))

        self._text_type: Optional[KeystoreTextType] = None
        self._text_matches: Optional[KeystoreMatchType] = None
        self._derivation_text = ""

        self._passphrase_label = QLabel(_("Passphrase"))
        self._passphrase_edit = QLineEdit()
        self._passphrase_edit.setFixedWidth(140)

        self._derivation_label = QLabel(_("Derivation path"))
        self._derivation_edit = QLineEdit(self._derivation_text)
        self._derivation_edit.setFixedWidth(140)
        self._derivation_edit.textEdited.connect(self._on_derivation_text_edited)

        self._options_label = QLabel(_("Options"))
        self._watchonly_button = QCheckBox(_("This is a watch-only account."))

        grid = QGridLayout()
        grid.addWidget(self._passphrase_label, 0, 0, Qt.AlignRight)
        grid.addWidget(self._passphrase_edit, 0, 1, Qt.AlignLeft)
        grid.addWidget(self._derivation_label, 1, 0, Qt.AlignRight)
        grid.addWidget(self._derivation_edit, 1, 1, Qt.AlignLeft)
        grid.addWidget(self._options_label, 2, 0, Qt.AlignRight)
        grid.addWidget(self._watchonly_button, 2, 1, Qt.AlignLeft)

        layout = QVBoxLayout()
        layout.addLayout(grid)
        self.setLayout(layout)

    def _on_derivation_text_edited(self, text: str) -> None:
        self._derivation_text = text.strip()
        self.completeChanged.emit()

    # Qt method called to determine if 'Next' or 'Finish' should be enabled or disabled.
    # Overriding this requires us to emit the 'completeChanges' signal where applicable.
    def isComplete(self) -> bool:
        if self._allow_derivation_path_usage():
            try:
                derivation = bip32_decompose_chain_string(self._derivation_text)
            except ValueError:
                return False
        return True

    # Qt method called when 'Next' or 'Finish' is clicked for last-minute validation.
    def validatePage(self) -> bool:
        # Called when 'Next' or 'Finish' is clicked for last-minute validation.
        assert self.isComplete()

        wizard: AccountWizard = self.wizard()
        if not self._create_account(main_window=wizard._main_window):
            return False
        return True

    # Qt method called to get the Id of the next page.
    def nextId(self) -> int:
        return -1

    def on_enter(self) -> None:
        wizard: AccountWizard = self.wizard()

        self._text_type = wizard.get_text_import_type()
        self._text_matches = wizard.get_text_import_matches()
        if self._text_type == KeystoreTextType.BIP39_SEED_WORDS:
            self._derivation_text = bip44_derivation_cointype(0, 0)
        else:
            self._derivation_text = ""

        self._passphrase_edit.setText("")
        self._passphrase_edit.setEnabled(self._allow_passphrase_usage())
        self._derivation_edit.setText(self._derivation_text)
        self._derivation_edit.setEnabled(self._allow_derivation_path_usage())
        self._watchonly_button.setChecked(False)
        self._watchonly_button.setEnabled(self._allow_watch_only_usage())

    def on_leave(self) -> None:
        pass

    def _allow_passphrase_usage(self) -> bool:
        return self._text_type in (KeystoreTextType.ELECTRUM_SEED_WORDS,
            KeystoreTextType.BIP39_SEED_WORDS)

    def _allow_derivation_path_usage(self) -> bool:
        return self._text_type in (KeystoreTextType.BIP39_SEED_WORDS,)

    def _allow_watch_only_usage(self) -> bool:
        return self._text_type in (KeystoreTextType.BIP39_SEED_WORDS,
            KeystoreTextType.ELECTRUM_SEED_WORDS, KeystoreTextType.EXTENDED_PRIVATE_KEY)

    @protected
    def _create_account(self, main_window: Optional[ElectrumWindow]=None,
            password: Optional[str]=None) -> bool:
        passphrase = (self._passphrase_edit.text().strip()
            if self._allow_passphrase_usage() else None)
        derivation_text = self._derivation_text if self._allow_derivation_path_usage() else None
        watch_only = (self._watchonly_button.isChecked()
            if self._allow_watch_only_usage() else False)

        _keystore = instantiate_keystore_from_text(self._text_type, self._text_matches,
            password, derivation_text, passphrase, watch_only)
        wizard: AccountWizard = self.wizard()
        return wizard.set_keystore_result(ResultType.IMPORTED, _keystore)


class CosignWidget(QWidget):
    size = 200

    def __init__(self, m: int, n: int) -> None:
        QWidget.__init__(self)
        self.setMinimumHeight(self.size)
        self.setMaximumHeight(self.size)
        self.m = m
        self.n = n

        self._green = QBrush(QColor.fromRgb(0x55, 0xDD, 0x55))

    def set_n(self, n: int) -> None:
        self.n = n
        self.update()

    def set_m(self, m: int) -> None:
        self.m = m
        self.update()

    def paintEvent(self, event) -> None:
        bgcolor = self.palette().color(QPalette.Background)
        pen = QPen(bgcolor, 7, Qt.SolidLine)
        qp = QPainter()
        qp.begin(self)
        qp.setPen(pen)
        qp.setRenderHint(QPainter.Antialiasing)
        qp.setBrush(Qt.gray)
        x = int((self.width() - self.size) / 2)
        for i in range(self.n):
            alpha = int(16 * 360 * i/self.n)
            alpha2 = int(16 * 360 * 1/self.n)
            qp.setBrush(self._green if i<self.m else Qt.gray)
            qp.drawPie(x, 0, self.size, self.size, alpha, alpha2)
        qp.end()


class CreateMultisigAccountPageContext(QObject):
    def __init__(self, page: QWizardPage, cosign_widget: CosignWidget,
            summary_label: QLabel) -> None:
        self.page = page
        self.cosign_widget = cosign_widget
        self.summary_label = summary_label

        self._watch_only = False

    def set_m(self, value: int) -> None:
        self.cosign_widget.set_m(value)
        self.update_summary_label()

    def set_n(self, value: int) -> None:
        self.cosign_widget.set_n(value)
        self.update_summary_label()

    def set_watch_only(self, value: bool) -> None:
        self._watch_only = value

    def update_summary_label(self) -> None:
        self.summary_label.setText(_("Every transaction must be signed by at least {} of the {} "
            "cosigners.").format(self.cosign_widget.m, self.cosign_widget.n))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "m": self.cosign_widget.m,
            "n": self.cosign_widget.n,
            "watch_only": False,
        }


class CreateMultisigAccountOptionsWidget(WizardFormSection):
    def __init__(self, form_context: Optional[CreateMultisigAccountPageContext],
            page: QWizardPage) -> None:
        super().__init__(page)

        self.setObjectName("CreateMultisigAccountOptions")
        self._form_context = form_context

        widget = QCheckBox(_("Watch only."))

        self.add_title(_("Additional options"))
        self.add_row(_("Account type")+":", widget)

        page.registerField("multisig-watch-only", widget, "checked")

    def set_form_context(self, form_context: CreateMultisigAccountPageContext) -> None:
        self._form_context = form_context


class CreateMultisigAccountSettingsWidget(WizardFormSection):
    def __init__(self, form_context: Optional[CreateMultisigAccountPageContext],
            page: QWizardPage) -> None:
        super().__init__(page)

        self.setObjectName("CreateMultisigAccountSettings")
        self._page = page
        self._form_context = form_context

        m_edit = QSlider(Qt.Horizontal, self)
        m_edit.setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Maximum)
        n_edit = QSlider(Qt.Horizontal, self)
        n_edit.setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Maximum)
        n_edit.setMinimum(2)
        n_edit.setMaximum(MAXIMUM_COSIGNER_COUNT)
        m_edit.setMinimum(1)
        m_edit.setMaximum(DEFAULT_COSIGNER_COUNT)
        n_edit.setValue(DEFAULT_COSIGNER_COUNT)
        m_edit.setValue(DEFAULT_COSIGNER_COUNT)
        n_label = QLabel()
        m_label = QLabel()

        def on_m(m: int) -> None:
            nonlocal m_label
            m_label.setText(_('Require %d signatures')%m +":")
            if self._form_context is not None:
                self._form_context.set_m(m)
        def on_n(n: int) -> None:
            nonlocal n_label, n_edit
            n_label.setText(_('From %d cosigners')%n +":")
            if self._form_context is not None:
                self._form_context.set_n(n)
            m_edit.setMaximum(n)
        n_edit.valueChanged.connect(on_n)
        m_edit.valueChanged.connect(on_m)
        # Prime the labels.
        on_n(DEFAULT_COSIGNER_COUNT)
        on_m(DEFAULT_COSIGNER_COUNT)

        self.add_row(n_label, n_edit, True)
        self.add_row(m_label, m_edit, True)

        page.registerField("multisig-m", m_edit, "value")
        page.registerField("multisig-n", n_edit, "value")

    def set_form_context(self, form_context: CreateMultisigAccountPageContext) -> None:
        self._form_context = form_context


class CreateMultisigAccountPage(QWizardPage):
    def __init__(self, wizard: AccountWizard):
        super().__init__(wizard)

        self.setTitle(_("Create a Multi-signature Account"))
        self.setFinalPage(False)

        self._next_page_id = AccountPage.CREATE_MULTISIG_ACCOUNT_COSIGNERS

        self._form_context: Optional[CreateMultisigAccountPageContext] = None
        self._cosign_widget = CosignWidget(DEFAULT_COSIGNER_COUNT, DEFAULT_COSIGNER_COUNT)
        self._summary_label = QLabel()
        self._settings_widget = CreateMultisigAccountSettingsWidget(self._form_context, self)

        layout = self._create_layout()
        self.setLayout(layout)

    def _create_form_context(self) -> None:
        self._form_context = CreateMultisigAccountPageContext(self, self._cosign_widget,
            self._summary_label)

    def _create_layout(self) -> QVBoxLayout:
        if self._form_context is not None:
            self._settings_widget.set_form_context(self._form_context)
            self._form_context.update_summary_label()

        vbox = QVBoxLayout()
        vbox.addStretch(1)
        vbox.addWidget(self._cosign_widget)
        vbox.addStretch(1)
        vbox.addWidget(self._summary_label, 0, Qt.AlignCenter)
        vbox.addStretch(1)
        vbox.addWidget(self._settings_widget)

        return vbox

    def on_enter(self) -> None:
        self._next_page_id = AccountPage.CREATE_MULTISIG_ACCOUNT_COSIGNERS

        button = self.wizard().button(QWizard.CustomButton1)
        button.setText(_("&Customize"))
        button.setContentsMargins(10, 0, 10, 0)
        button.clicked.connect(self._on_customize_button_clicked)
        button.setVisible(True)
        # The customize page is disabled for now. It's one option was obsoleted.
        button.setEnabled(False)

        self._create_form_context()
        discardable = QWidget()
        discardable.setLayout(self.layout())
        self.setLayout(self._create_layout())

    def on_leave(self) -> None:
        button = self.wizard().button(QWizard.CustomButton1)
        button.clicked.disconnect()
        button.setVisible(False)

        self._form_context = None

    # Qt method called to get the Id of the next page.
    def nextId(self):
        # Need to know if customize is clicked and go to the custom page.
        return self._next_page_id

    def _on_customize_button_clicked(self, *checked) -> None:
        assert self.isComplete()
        self._next_page_id = AccountPage.CREATE_MULTISIG_ACCOUNT_CUSTOM

        wizard: AccountWizard = self.wizard()
        wizard.next()


class CreateMultisigAccountCustomPage(QWizardPage):
    def __init__(self, wizard: AccountWizard):
        super().__init__(wizard)

        self.setTitle(_("Create a Multi-signature Account - Customize"))
        self.setFinalPage(False)

        self._next_page_id = AccountPage.CREATE_MULTISIG_ACCOUNT_COSIGNERS

        self._form_context: Optional[CreateMultisigAccountPageContext] = None
        self._cosign_widget = CosignWidget(2, 2)
        self._summary_label = QLabel()
        self._options_widget = CreateMultisigAccountOptionsWidget(self._form_context, self)

        layout = self._create_layout()
        self.setLayout(layout)

    def _create_form_context(self) -> None:
        self._form_context = CreateMultisigAccountPageContext(self, self._cosign_widget,
            self._summary_label)

    def _create_layout(self,
            form_context: Optional[CreateMultisigAccountPageContext]=None) -> QVBoxLayout:
        # Object creation has no context.
        if self._form_context is not None:
            self._options_widget.set_form_context(self._form_context)
            self._form_context.set_m(self.field("multisig-m"))
            self._form_context.set_n(self.field("multisig-n"))
            self._form_context.update_summary_label()

        vbox = QVBoxLayout()
        vbox.addStretch(1)
        vbox.addWidget(self._cosign_widget)
        vbox.addStretch(1)
        vbox.addWidget(self._summary_label, 0, Qt.AlignCenter)
        vbox.addStretch(1)
        vbox.addWidget(self._options_widget)

        return vbox

    def on_enter(self) -> None:
        self._next_page_id = AccountPage.CREATE_MULTISIG_ACCOUNT_COSIGNERS

        self._create_form_context()
        discardable = QWidget()
        discardable.setLayout(self.layout())
        self.setLayout(self._create_layout())

    def on_leave(self) -> None:
        self._form_context = None

    # Qt method called to get the Id of the next page.
    def nextId(self):
        # Need to know if customize is clicked and go to the custom page.
        return self._next_page_id


class MultisigAccountCosignerListPage(QWizardPage):
    _cosigner_states: List[CosignerState] = []

    _list: Optional[QListWidget] = None

    def __init__(self, wizard: AccountWizard) -> None:
        super().__init__(wizard)

        self.setTitle(_("Create a Multi-signature Account") +" - "+ _("Cosigners"))

        self._list = CosignerList(wizard.get_main_window())

        vbox = QVBoxLayout()
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)
        vbox.addWidget(self._list)
        self.setLayout(vbox)

    def on_enter(self) -> None:
        self._list.clear()
        cosigner_count = self.field("multisig-n")
        self._cosigner_states = [ CosignerState(i) for i in range(cosigner_count) ]

        for cosigner_state in self._cosigner_states:
            card = self._list.add_state(cosigner_state)
            card.cosigner_updated.connect(self.event_cosigner_updated)

    def on_leave(self) -> None:
        pass

    # Qt method called when 'Next' or 'Finish' is clicked for last-minute validation.
    def validatePage(self) -> bool:
        assert len(self._cosigner_states) == self.field("multisig-n"), "Mismatched cosigner counts"

        keystore = Multisig_KeyStore({
            "m": self.field("multisig-m"),
            "n": self.field("multisig-n"),
            "cosigner-keys": [],
        })
        for state in self._cosigner_states:
            assert state.keystore is not None, f"Expected complete keystore {state.cosigner_index}"
            keystore.add_cosigner_keystore(state.keystore)

        wizard: AccountWizard = self.wizard()
        wizard.set_keystore_result(ResultType.MULTISIG, keystore)
        return True

    # Qt method called to get the Id of the next page.
    def nextId(self) -> int:
        return -1

    # Qt method called to determine if 'Next' or 'Finish' should be enabled or disabled.
    # Overriding this requires us to emit the 'completeChanges' signal where applicable.
    def isComplete(self) -> bool:
        return len(self._cosigner_states) and all(s.is_complete() for s in self._cosigner_states)

    def event_cosigner_updated(self, cosigner_index: int) -> None:
        self.completeChanged.emit()

