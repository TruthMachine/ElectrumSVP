from typing import Optional
import weakref

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QComboBox, QDialog, QLabel, QLineEdit, QVBoxLayout, QWidget,
    QScrollArea, QSizePolicy
)

from electrumsv.constants import DerivationType, KeystoreType, ScriptType
from electrumsv.i18n import _
from electrumsv.wallet import Wallet

from .cosigners_view import CosignerState, CosignerList
from .main_window import ElectrumWindow
from .qrtextedit import ShowQRTextEdit
from .util import Buttons, CloseButton, FormSectionWidget


class AccountDialog(QDialog):
    _list: Optional[CosignerList] = None

    def __init__(self, main_window: ElectrumWindow, wallet: Wallet, account_id: int,
                 parent: QWidget) -> None:
        super().__init__(parent, Qt.WindowSystemMenuHint | Qt.WindowTitleHint |
                         Qt.WindowCloseButtonHint)

        assert type(main_window) is weakref.ProxyType
        self._main_window = main_window
        self._wallet = wallet

        self._account = account = self._wallet.get_account(account_id)
        keystore = account.get_keystore()

        self.setWindowTitle(_("Account Information"))

        # Tweak initial size: taller than before
        self.resize(650, 700)
        self.setMinimumSize(600, 500)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        vbox = QVBoxLayout()
        self._form = form = FormSectionWidget()

        # Account name
        name_widget = QLineEdit()
        name_widget.setText(account.display_name())
        name_widget.setReadOnly(True)
        form.add_row(_("Account name"), name_widget, True)

        # Account type and keystore
        form.add_row(_("Account type"), QLabel(account.type().value))
        if keystore is not None:
            form.add_row(_("Keystore type"), QLabel(keystore.type().value))

        # Hardware derivation path
        if keystore is not None and keystore.type() == KeystoreType.HARDWARE:
            form.add_row(_("Derivation path"), QLabel(keystore.derivation))

        # Script type combo
        script_type_combo = QComboBox()

        def update_script_types() -> None:
            default_script_type = account.get_default_script_type()
            combo_items = [v.name for v in account.get_enabled_script_types()]
            script_type_combo.clear()
            script_type_combo.addItems(combo_items)
            script_type_combo.setCurrentIndex(script_type_combo.findText(default_script_type.name))

        def on_script_type_change(_index: int) -> None:
            script_type_name = script_type_combo.currentText()
            new_script_type = getattr(ScriptType, script_type_name)
            current_script_type = account.get_default_script_type()
            if current_script_type != new_script_type:
                account.set_default_script_type(new_script_type)
                view = self._main_window.get_receive_view(account.get_id())
                view.update_destination()

        if not account.is_watching_only():
            script_type_combo.currentIndexChanged.connect(on_script_type_change)

        update_script_types()
        # Prevent changing script type
        script_type_combo.setEnabled(False)
        form.add_row(_("Script type"), script_type_combo, True)

        vbox.addWidget(form)

        # Multi-sig or deterministic account display
        add_stretch = True
        if keystore is not None:
            if keystore.derivation_type == DerivationType.ELECTRUM_MULTISIG:
                # Multi-signature summary
                multisig_form = FormSectionWidget(minimum_label_width=160)
                multisig_form.add_title(_("Multi-signature properties"))
                multisig_form.add_row(_("Number of cosigners"), QLabel(str(keystore.n)))
                multisig_form.add_row(_("Number of signatures required"), QLabel(str(keystore.m)))
                vbox.addWidget(multisig_form)

                # Cosigner list wrapped in scroll area
                self._list = list = CosignerList(self._main_window, create=False)
                for i, ks in enumerate(account.get_keystores()):
                    state = CosignerState(i, ks)
                    list.add_state(state)

                scroll = QScrollArea()
                scroll.setWidgetResizable(True)
                scroll.setWidget(list)
                scroll.setMinimumHeight(350)  # more visible by default
                scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
                vbox.addWidget(scroll, 1)  # stretch factor ensures it fills space
                add_stretch = False

            elif account.is_deterministic():
                mpk_list = account.get_master_public_keys()
                mpk_text = ShowQRTextEdit()
                mpk_text.setFixedHeight(65)
                mpk_text.addCopyButton(self._main_window.app)
                mpk_text.setText(mpk_list[0])
                mpk_text.repaint()  # macOS hack for Electrum #4777
                form.add_row(QLabel(_("Master public key")), mpk_text, True)

        if add_stretch:
            vbox.addStretch(1)

        # Close button
        buttons = Buttons(CloseButton(self))
        self._buttons = buttons
        vbox.addLayout(self._buttons)

        self.setLayout(vbox)

