import os
import random
from typing import List

from PyQt5.QtCore import Qt, QTimer, QSize
from PyQt5.QtWidgets import (
    QAbstractItemView, QDialog, QLabel, QSpinBox, QVBoxLayout, QWidget,
    QComboBox, QLineEdit, QTreeWidget, QTreeWidgetItem, QMenu,
    QPushButton, QHBoxLayout, QCheckBox, QSizePolicy
)

from electrumsv.constants import RECEIVING_SUBPATH, DEFAULT_FEE
from electrumsv.i18n import _
from electrumsv.wallet import Wallet
from electrumsv.app_state import app_state

from electrumsv.transaction import XTxOutput, tx_output_to_display_text

from .amountedit import BTCAmountEdit, BTCSatsByteEdit
from .main_window import ElectrumWindow
from .util import (
    Buttons, ButtonsTableWidget, CloseButton, FormSectionWidget,
    HelpDialogButton, MessageBox, ColorScheme, MyTreeWidget,
    update_fixed_tree_height, read_QIcon
)
from bitcoinx import hash_to_hex_str
from electrumsv.wallet import UTXO
from electrumsv.exceptions import NotEnoughFunds



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
        self._frozen_icon = read_QIcon("lockflake")

        self.setWindowTitle(_("Coin/UTXO Split"))
        self.setMinimumSize(675, 650)

        # --- Controls ---
        self._quantity_widget = quantity_widget = QSpinBox()
        quantity_widget.setMinimum(1)
        quantity_widget.setMaximum(1000)
        quantity_widget.setValue(10)
        quantity_widget.valueChanged.connect(self._on_quantity_changed)

        self._mode_combo = QComboBox()
        self._mode_combo.addItem(_("Equal Split"))
        self._mode_combo.addItem(_("Random Split"))
        self._mode_combo.setCurrentIndex(1)

        self._mode_combo.currentIndexChanged.connect(self._refresh)

        self._amount_input = QLineEdit()
        self._amount_input.setPlaceholderText(_("Amount"))
        self._amount_input.textChanged.connect(self._refresh)

        self._max_button = QPushButton(_("Max"))
        self._max_button.clicked.connect(self._on_max_button_click)

        self._fee_rate_e = BTCSatsByteEdit(self)
        self._fee_rate_e.setAmount(app_state.config.fee_per_kb())

        self._recommended_fee_checkbox = QCheckBox(
            _('Use recommended fee')
        )
        self._recommended_fee_checkbox.setChecked(
            app_state.config.fee_per_kb() == DEFAULT_FEE
        )

        self._fee_e = BTCAmountEdit(self)
        self._fee_e.setReadOnly(True)

        self._fee_update_timer = QTimer(self)
        self._fee_update_timer.setSingleShot(True)
        self._fee_update_timer.timeout.connect(self._refresh)

        self._quantity_update_timer = QTimer(self)
        self._quantity_update_timer.setSingleShot(True)
        self._quantity_update_timer.timeout.connect(self._refresh)

        # --- Layout ---
        vbox = QVBoxLayout()

        form = FormSectionWidget(minimum_label_width=120)
        form.add_title(_("Options"))
        form.add_row(_("# of output destinations"), quantity_widget)
        form.add_row(_("Mode"), self._mode_combo)
        amount_layout = QHBoxLayout()
        amount_layout.setContentsMargins(0, 0, 0, 0)
        amount_layout.addWidget(self._amount_input)
        amount_layout.addSpacing(8)
        amount_layout.addWidget(self._max_button)

        form.add_row(_("Amount"), amount_layout)

        fee_rate_layout = QHBoxLayout()
        fee_rate_layout.setContentsMargins(0, 0, 0, 0)
        fee_rate_layout.addWidget(self._fee_rate_e)
        fee_rate_layout.addSpacing(8)
        fee_rate_layout.addWidget(self._recommended_fee_checkbox)

        self._fee_rate_e.textEdited.connect(self._on_fee_rate_edited)

        self._recommended_fee_checkbox.stateChanged.connect(
            self._on_recommended_fee_changed
        )

        self._fee_rate_e.editingFinished.connect(
            self._on_fee_rate_editing_finished
        )

        form.add_row(_("Fee Rate"), fee_rate_layout)

        form.add_row(_("Tx Fee Amount"), self._fee_e)
        form.frame_layout.setSpacing(0)


        # --- Selected From ---
        self._from_label = QLabel(_("From:"), self)
        self._from_label.setContentsMargins(0, 2, 0, 2)
        self._from_label.setAlignment(Qt.AlignLeft)
        vbox.addWidget(self._from_label)


        self._from_table = QTreeWidget()
        self._from_table.setColumnCount(4)
        self._from_table.setHeaderLabels([
            _("Unspent coins/UTXOs"), _("Derivation"), _("Amount"), _("Frozen")
        ])
        self._from_table.setIconSize(QSize(20, 20))
        self._from_table.setUniformRowHeights(True)
        self._from_table.setStyleSheet("QTreeWidget::item { height: 18px; }")
        self._from_table.setRootIsDecorated(True)
        self._from_table.setSortingEnabled(True)
        self._from_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._from_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._from_table.customContextMenuRequested.connect(
            self._from_context_menu
        )
        self._from_table.setAlternatingRowColors(False)
        self._from_table.header().setStretchLastSection(False)

        self._from_table.header().setSectionResizeMode(
            0, self._from_table.header().Stretch
        )
        self._from_table.header().setSectionResizeMode(
            1, self._from_table.header().ResizeToContents
        )
        self._from_table.header().setSectionResizeMode(
            2, self._from_table.header().ResizeToContents
        )

        self._from_table.header().setSectionResizeMode(
            3, self._from_table.header().ResizeToContents
        )

        self._from_table.setMaximumHeight(180)

        vbox.addWidget(self._from_table)



        self._from_list = QTreeWidget(self)
        self._from_list.setColumnCount(2)
        self._from_list.setHeaderLabels([
            _("Address / Outpoint"),
            _("Amount")
        ])
        self._from_list.setMaximumHeight(100)
        self._from_list.setSelectionMode(QAbstractItemView.SingleSelection)

        header = self._from_list.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, header.Stretch)
        header.setSectionResizeMode(1, header.ResizeToContents)

        from_layout = QHBoxLayout()
        from_layout.setContentsMargins(0, 0, 0, 0)
        from_layout.addWidget(self._from_list)

        self._clear_from_button = QPushButton(_("Clear"), self)
        self._clear_from_button.clicked.connect(self._clear_spend_from)
        self._clear_from_button.setHidden(True)
        from_layout.addWidget(self._clear_from_button)

        vbox.addLayout(from_layout)

        # Nothing selected initially.
        self._from_list.setHidden(True)


        vbox.addWidget(form)

        self._to_label = QLabel(_("To:"), self)
        self._to_label.setContentsMargins(0, 2, 0, 2)
        self._to_label.setAlignment(Qt.AlignLeft)
        vbox.addWidget(self._to_label)

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

        self._preview_button = QPushButton(_("Preview Pay-to-Many"), self)
        self._preview_button.clicked.connect(self._on_send_button_click)

        buttons = Buttons(self._preview_button)
        buttons.add_left_button(
            HelpDialogButton(self, "misc", "payment-destinations-dialog")
        )
        vbox.addLayout(buttons)

        self.setLayout(vbox)

        self._entries: List[str] = []
        self._selected_spend_coins = []
        self._not_enough_funds = False
        self._is_max = False

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

        if not text:
            return [0] * count

        try:
            value = int(
                round(float(text) * (10 ** app_state.decimal_point))
            )
        except ValueError:
            return [0] * count


        if mode == 0:
            if value < count * MIN_SATS_PER_OUTPUT:
                return [0] * count

            each = max(MIN_SATS_PER_OUTPUT, value // count)
            return [each] * count

        elif mode == 1:
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
                "Large transactions may increase wallet latency.\n"
                "Proceed with caution."
            ))

        text = self._get_text()
        account = self._wallet.get_account(self._account_id)

        # Carry the fee rate used by Payment Destinations into SendView.
        fee_rate = self._fee_rate_e.get_amount()

        self._main_window.set_active_account(account)
        self._main_window.show_send_tab()

        def apply_text():
            send_view = self._main_window.get_send_view(self._account_id)

            # Carry the selected UTXOs into the Send tab.
            send_view.set_pay_from(self._selected_spend_coins)

            # Carry the fee rate used for this Pay-to-Many transaction.
            if fee_rate is not None:
                send_view._fee_rate_e.setAmount(fee_rate)

            send_view._payto_e.paytomany()
            send_view._payto_e.setText(text)

        QTimer.singleShot(0, apply_text)
        self.close()


    def _on_recommended_fee_changed(self, state: int) -> None:
        if state == Qt.Checked:
            self._fee_rate_e.setAmount(DEFAULT_FEE)
            self._fee_rate_e.setReadOnly(True)
            app_state.config.set_key('customfee', DEFAULT_FEE)
        else:
            self._fee_rate_e.setReadOnly(False)

        if self._is_max:
            self._is_max = False
            self._amount_input.clear()
            self._max_button.setDisabled(False)

        self._fee_update_timer.start(500)


    def _on_fee_rate_editing_finished(self) -> None:
        if not self._fee_rate_e.text().strip():
            self._recommended_fee_checkbox.setChecked(True)
            return

        fee_rate = self._fee_rate_e.get_amount()

        if fee_rate is not None:
            app_state.config.set_key('customfee', fee_rate)


    def _on_fee_rate_edited(self, text: str) -> None:
        if self._recommended_fee_checkbox.isChecked():
            self._recommended_fee_checkbox.setChecked(False)

        if self._is_max:
            self._is_max = False
            self._amount_input.clear()
            self._max_button.setDisabled(False)

        if not text.strip():
            self._fee_e.clear()
            return

        self._fee_update_timer.start(500)

    def _on_max_button_click(self) -> None:
        # If the user has explicitly selected UTXOs with "Spend From",
        # calculate Max using only those UTXOs. Otherwise use all
        # spendable UTXOs as before.
        if self._selected_spend_coins:
            coins = list(self._selected_spend_coins)
        else:
            coins = self._account.get_spendable_coins(
                None,
                self._main_window.config
            )

        if not coins:
            self._amount_input.clear()
            return

        # Keep the selected Spend From coins intact. If there was no
        # previous Spend From selection, this establishes the normal
        # unrestricted Max selection.
        self._selected_spend_coins = list(coins)

        count = self._quantity_widget.value()
        keyinstances = self._get_receiving_keys(count)

        if not keyinstances:
            self._amount_input.clear()
            return

        outputs = []

        for index, keyinstance in enumerate(keyinstances):
            template = self._account.get_script_template_for_id(
                keyinstance.keyinstance_id
            )

            if template is None:
                continue

            amount = all if index == 0 else 0

            script = template.to_script()
            output = XTxOutput(amount, script)
            outputs.append(output)

        if not outputs:
            self._amount_input.clear()
            return

        tx = self._calculate_transaction(coins, outputs)

        if tx is None:
            self._amount_input.clear()
            return

        max_amount = tx.output_value()

        self._is_max = True

        self._amount_input.setText(
            self._format_amount(max_amount)
        )

        # Preserve and display the selected Spend From UTXOs.
        if self._selected_spend_coins:
            self._redraw_from_list()
            self._clear_from_button.setHidden(False)

        # Keep the selected UTXOs highlighted in the coin-control table.
        self._from_table.clearSelection()

        for i in range(self._from_table.topLevelItemCount()):
            address_item = self._from_table.topLevelItem(i)

            for j in range(address_item.childCount()):
                utxo_item = address_item.child(j)
                utxo = utxo_item.data(0, Qt.UserRole)

                if utxo in self._selected_spend_coins:
                    utxo_item.setSelected(True)
                    address_item.setExpanded(True)

        self._refresh()



    def _get_max_coins(self):
        return self._account.get_spendable_coins(
            None,
            self._main_window.config
        )



    # ------------------------
    # From (coin control)
    # ------------------------

    def _get_selected_from_utxos(self):
        selected = []

        for item in self._from_table.selectedItems():
            # Child row = individual UTXO.
            utxo = item.data(0, Qt.UserRole)

            if utxo is not None:
                if utxo not in selected:
                    selected.append(utxo)
                continue

            # Parent row = address. Select all of its UTXOs.
            for i in range(item.childCount()):
                child = item.child(i)
                utxo = child.data(0, Qt.UserRole)

                if utxo is not None and utxo not in selected:
                    selected.append(utxo)

        return selected


    def _from_context_menu(self, position) -> None:
        coins = self._get_selected_from_utxos()

        menu = QMenu(self)

        # Selection-specific actions.
        if coins:
            # Only unfrozen UTXOs can be used for Spend From.
            spendable_coins = [
                coin for coin in coins
                if not self._account.is_frozen_utxo(coin)
            ]

            if spendable_coins:
                menu.addAction(
                    _("Spend From"),
                    lambda: self._spend_from_coins(spendable_coins)
                )
            else:
                # All selected UTXOs are frozen.
                spend_action = menu.addAction(_("Spend From"))
                spend_action.setEnabled(False)

            menu.addSeparator()

            any_frozen = any(
                self._account.is_frozen_utxo(coin)
                for coin in coins
            )

            all_frozen = all(
                self._account.is_frozen_utxo(coin)
                for coin in coins
            )

            if not all_frozen:
                menu.addAction(
                    _("Freeze"),
                    lambda: self._freeze_from_coins(coins, True)
                )

            if any_frozen:
                menu.addAction(
                    _("Unfreeze"),
                    lambda: self._freeze_from_coins(coins, False)
                )

        # Provide "Unfreeze All" only when there are frozen UTXOs.
        all_coins = self._account.get_utxos()
        frozen_coins = [
            coin for coin in all_coins
            if self._account.is_frozen_utxo(coin)
        ]

        if frozen_coins:
            if coins:
                menu.addSeparator()

            def unfreeze_all() -> None:
                self._freeze_from_coins(frozen_coins, False)

            menu.addAction(
                _("Unfreeze All"),
                unfreeze_all
            )

        # Don't show an empty context menu.
        if menu.actions():
            menu.exec_(
                self._from_table.viewport().mapToGlobal(position)
            )



    def _freeze_from_coins(self, coins, freeze: bool) -> None:
        self._main_window.set_frozen_coin_state(
            self._account,
            coins,
            freeze
        )

        self._refresh_from_table()


    def _spend_from_coins(self, coins) -> None:
        if not coins:
            return

        # Store the selected UTXOs.
        self._selected_spend_coins = list(coins)

        # Make sure the selected coins can satisfy the minimum
        # amount required for the requested number of outputs.
        total = sum(coin.value for coin in coins)
        count = self._quantity_widget.value()
        minimum_required = count * MIN_SATS_PER_OUTPUT

        if total < minimum_required:

            MessageBox.show_error(
                _(
                    f"The selected coins contain {self._format_amount(total)}, "
                    f"but {count} outputs require at least "
                    f"{self._format_amount(minimum_required)}."
                ),
                parent=self
            )
            return

        # Calculate the maximum amount that can be sent from these
        # specific coins, including the transaction fee.
        keyinstances = self._get_receiving_keys(count)



        if not keyinstances:
            return

        outputs = []

        for index, keyinstance in enumerate(keyinstances):
            template = self._account.get_script_template_for_id(
                keyinstance.keyinstance_id
            )

            if template is None:
                continue

            amount = all if index == 0 else 0
            outputs.append(
                XTxOutput(amount, template.to_script())
            )

        if not outputs:
            return

        tx = self._calculate_transaction(
            self._selected_spend_coins,
            outputs
        )

        if tx is None:
            self._amount_input.clear()
            return

        max_amount = tx.output_value()

        self._amount_input.setText(
            self._format_amount(max_amount)
        )
        self._refresh()

        # Display the selected UTXOs in the Send-style From list.
        self._redraw_from_list()
        self._clear_from_button.setHidden(False)

        # Grow the dialog if the selected UTXO list needs additional space.
        self.layout().activate()
        required_height = self.layout().sizeHint().height()

        if required_height > self.height():
            self.resize(self.width(), required_height)

        # Highlight the selected UTXOs in the coin-control table.
        self._from_table.clearSelection()

        for i in range(self._from_table.topLevelItemCount()):
            address_item = self._from_table.topLevelItem(i)

            for j in range(address_item.childCount()):
                utxo_item = address_item.child(j)
                utxo = utxo_item.data(0, Qt.UserRole)

                if utxo in self._selected_spend_coins:
                    utxo_item.setSelected(True)

                    # Make sure the address containing the selected
                    # UTXO is visible.
                    address_item.setExpanded(True)

    def _clear_spend_from(self) -> None:
        self._selected_spend_coins = []

        self._from_list.clear()
        self._from_list.setHidden(True)
        self._clear_from_button.setHidden(True)

        self._from_table.clearSelection()

        self._amount_input.clear()
        self._fee_e.clear()

        self._not_enough_funds = False
        self._is_max = False
        self._max_button.setDisabled(False)



    def _redraw_from_list(self) -> None:
        self._from_list.clear()
        self._from_list.setHidden(len(self._selected_spend_coins) == 0)
        self._from_label.setHidden(False)

        def format_utxo(utxo) -> str:
            from bitcoinx import hash_to_hex_str

            h = hash_to_hex_str(utxo.tx_hash)

            return '{}...{}:{:d}\t{}'.format(
                h[0:10],
                h[-10:],
                utxo.out_index,
                utxo.address
            )

        for utxo in self._selected_spend_coins:
            self._from_list.addTopLevelItem(
                QTreeWidgetItem([
                    format_utxo(utxo),
                    app_state.format_amount(utxo.value)
                ])
            )






    def _refresh_from_table(self) -> None:
        # Remember which address rows are currently expanded.
        expanded_addresses = set()

        for i in range(self._from_table.topLevelItemCount()):
            item = self._from_table.topLevelItem(i)
            if item.isExpanded():
                expanded_addresses.add(item.text(0))

        self._from_table.clear()

        # Get the actual UTXOs for this account.
        utxos = self._account.get_utxos()

        # Group UTXOs by keyinstance.
        keyinstance_utxos = {}

        for utxo in utxos:
            keyinstance_id = getattr(utxo, "keyinstance_id", None)

            if keyinstance_id is None:
                continue

            keyinstance_utxos.setdefault(keyinstance_id, []).append(utxo)

        # Sort addresses by keyinstance ID so the wallet's natural
        # derivation order is used rather than alphabetical address order.
        for keyinstance_id in sorted(keyinstance_utxos):
            coins = keyinstance_utxos[keyinstance_id]

            try:
                keyinstance = self._account.get_keyinstance(keyinstance_id)
                address = str(getattr(keyinstance, "address", ""))

                if not address:
                    for coin in coins:
                        address = str(getattr(coin, "address", ""))
                        if address:
                            break

            except Exception:
                address = str(getattr(coins[0], "address", ""))

            # Get the key's derivation path.
            try:
                derivation = self._account.get_derivation_path_text(
                    keyinstance_id
                )

                keystore = self._account.get_keystore()
                derivation_base = getattr(keystore, "derivation", None)

                if derivation_base and derivation.startswith("m/"):
                    subpath = derivation[2:]
                    derivation = f"{derivation_base}/{subpath}"

                    # Branch 1 is the change branch.
                    if subpath.startswith("1/"):
                        derivation += " (change)"

            except Exception:
                derivation = ""


            total = sum(utxo.value for utxo in coins)

            address_has_frozen = any(
                self._account.is_frozen_utxo(utxo)
                for utxo in coins
            )

            address_item = QTreeWidgetItem([
                address,
                derivation,
                app_state.format_amount(total),
                ""
            ])

            if address_has_frozen:
                address_item.setIcon(3, self._frozen_icon)

            # Highlight the address if any of its UTXOs are frozen.
            try:
                if any(self._account.is_frozen_utxo(utxo) for utxo in coins):
                    for col in range(4):
                        address_item.setBackground(
                            col,
                            ColorScheme.ORANGE.as_color(True)
                        )
            except Exception:
                pass

            # Add the actual UTXOs as child rows.
            for utxo in coins:
                try:
                    outpoint = utxo.key_str()
                except Exception:
                    outpoint = ""

                utxo_frozen = self._account.is_frozen_utxo(utxo)

                utxo_amount = app_state.format_amount(utxo.value)


                utxo_item = QTreeWidgetItem([
                    f"({app_state.format_amount(utxo.value)}) {outpoint}",
                    "",
                    "",
                    ""
                ])

                if utxo_frozen:
                    utxo_item.setIcon(3, self._frozen_icon)


                # Keep the actual UTXO attached to the row.
                utxo_item.setData(0, Qt.UserRole, utxo)

                # Highlight frozen coins in orange.
                try:
                    if utxo_frozen:
                        for col in range(4):
                            utxo_item.setBackground(
                                col,
                                ColorScheme.ORANGE.as_color(True)
                            )
                except Exception:
                    pass

                address_item.addChild(utxo_item)

            self._from_table.addTopLevelItem(address_item)

            # Restore the user's previous expanded/collapsed state.
            if address in expanded_addresses:
                address_item.setExpanded(True)




    # ------------------------
    # Refresh (FINAL WORKING)
    # ------------------------

    def _on_quantity_changed(self, value: int) -> None:
        coins = self._account.get_spendable_coins(
            None,
            self._main_window.config
        )

        if coins:
            total = sum(coin.value for coin in coins)
            minimum_required = value * MIN_SATS_PER_OUTPUT

            if total < minimum_required:
                MessageBox.show_warning(
                    _(
                        f"You have {self._format_amount(total)} available, "
                        f"but {value} outputs require at least "
                        f"{self._format_amount(minimum_required)} "
                        f"at {MIN_SATS_PER_OUTPUT} satoshis per output."
                    ),
                    parent=self
                )

        self._quantity_update_timer.start(500)

    def _calculate_transaction(self, coins, outputs):
        self._not_enough_funds = False

        try:
            fee_rate = self._fee_rate_e.get_amount()

            tx = self._account.make_unsigned_transaction(
                coins,
                outputs,
                self._main_window.config,
                None,
                fee_rate
            )

            return tx

        except NotEnoughFunds as e:
            self._not_enough_funds = True
            print("\n*** PAYMENT DESTINATIONS: NOT ENOUGH FUNDS ***")
            print("Exception:", repr(e))
            print("Coins:", len(coins))
            print("Outputs:", len(outputs))
            print("Fee rate:", fee_rate)
            return None

        except Exception as e:
            import traceback
            print("\n*** PAYMENT DESTINATIONS: TRANSACTION ERROR ***")
            print("Exception:", repr(e))
            print("Coins:", len(coins))
            print("Outputs:", len(outputs))
            print("Fee rate:", fee_rate)
            traceback.print_exc()
            return None

    def _refresh(self) -> None:
        count = self._quantity_widget.value()

        keyinstances = self._get_receiving_keys(count)
        if not keyinstances:
            self._from_table.clear()
            return

        self._refresh_from_table()

        amounts = self._get_amounts(len(keyinstances))

        self._table.clear()
        self._table.setColumnCount(2)
        self._table.setHorizontalHeaderLabels([_("Destination"), _("Amount")])
        self._table.setRowCount(len(keyinstances))

        hh = self._table.horizontalHeader()
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(0, hh.Stretch)
        hh.setSectionResizeMode(1, hh.ResizeToContents)

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
                        # THIS IS THE ONLY CORRECT WAY
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
                self._table.setCellWidget(
                    row, 1, QLabel(self._format_amount(amount))
                )
            else:
                self._table.setCellWidget(row, 1, QLabel(""))

        # Calculate the actual transaction using the selected UTXOs.
        coins = self._selected_spend_coins

        if coins and any(amount > 0 for amount in amounts):
            outputs = []

            for keyinstance, amount in zip(keyinstances, amounts):
                if amount <= 0:
                    continue

                template = self._account.get_script_template_for_id(
                    keyinstance.keyinstance_id
                )

                if template is not None:
                    outputs.append(
                        XTxOutput(amount, template.to_script())
                    )

            tx = self._calculate_transaction(coins, outputs)


            if tx is not None:
                fee = tx.get_fee()

                self._fee_e.setText(
                    app_state.format_amount(fee)
                )
            else:
                self._fee_e.clear()

            if self._not_enough_funds:
                self._amount_input.setStyleSheet("color: #ff6666;")
            else:
                self._amount_input.setStyleSheet("")

        else:
            self._fee_e.clear()

