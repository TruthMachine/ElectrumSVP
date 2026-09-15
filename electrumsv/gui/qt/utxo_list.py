#!/usr/bin/env python
#
# Electrum - lightweight Bitcoin client
# Copyright (C) 2015 Thomas Voegtlin
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation files
# (the "Software"), to deal in the Software without restriction,
# including without limitation the rights to use, copy, modify, merge,
# publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
# BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
# ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from typing import List, Optional
import weakref

from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QAbstractItemView, QMenu, QWidget

from electrumsv.app_state import app_state
from electrumsv.i18n import _
from electrumsv.platform import platform
from electrumsv.util import profiler
from electrumsv.wallet import AbstractAccount, UTXO

from .main_window import ElectrumWindow
from .util import SortableTreeWidgetItem, MyTreeWidget, ColorScheme, read_QIcon



class UTXOList(MyTreeWidget):
    filter_columns = [0, 2]  # Address, Label

    def __init__(self, parent: QWidget, main_window: ElectrumWindow) -> None:
        MyTreeWidget.__init__(self, parent, main_window, self.create_menu, [
            _('Output point'), _('Label'), _('Amount'), _('Height'), _('Frozen')], 1)

        self.setAlternatingRowColors(False)

        self._main_window = weakref.proxy(main_window)
        self._wallet = main_window._wallet
        self._account_id: Optional[int] = None
        self._account: Optional[AbstractAccount] = None

        self._main_window.account_change_signal.connect(self._on_account_change)

        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setSortingEnabled(True)
        self.setIconSize(QSize(20, 20))
        self.setUniformRowHeights(True)
        self.setStyleSheet("QTreeWidget::item { height: 18px; }")

        self._monospace_font = QFont(platform.monospace_font)
        self._frozen_icon = read_QIcon("lockflake")

    def on_account_change(self, new_account_id: int) -> None:
        self._account_id = new_account_id
        self._account = self._main_window._wallet.get_account(new_account_id)

    def _on_account_change(self, new_account_id: int, new_account: AbstractAccount) -> None:
        self.clear()
        old_account_id = self._account_id
        self._account_id = new_account_id
        self._account = new_account

    def update(self) -> None:
        self._on_update_utxo_list()


    @profiler
    def _on_update_utxo_list(self):
        if self._account_id is None:
            return

        prev_selection = self.get_selected()  # cache previous selection, if any
        self.clear()

        for utxo in self._account.get_utxos():
            # Safely get metadata (height) for display
            try:
                metadata = self._account.get_transaction_metadata(utxo.tx_hash)
                height_str = str(metadata.height)
            except Exception:
                height_str = ""

            # Shorten prevout for display
            try:
                prevout_str = utxo.key_str()
                prevout_str = prevout_str[0:10] + '...' + prevout_str[-2:]
            except Exception:
                prevout_str = ""

            # Prefer address/key label; fallback to tx label; robust against missing data
            label = ""
            try:
                key_id = getattr(utxo, "keyinstance_id", None)
                if key_id is not None:
                    # Use account helper if available (safer than touching keyinstance object)
                    if hasattr(self._account, "get_keyinstance_label"):
                        label = self._account.get_keyinstance_label(key_id) or ""
                    else:
                        ki = self._account.get_keyinstance(key_id)
                        label = getattr(ki, "label", "") or ""

                if not label:
                    # Fall back to the previous behaviour (transaction label)
                    label = self._wallet.get_transaction_label(utxo.tx_hash) or ""
            except Exception:
                # If anything goes wrong, try the tx label as last resort, otherwise empty
                try:
                    label = self._wallet.get_transaction_label(utxo.tx_hash) or ""
                except Exception:
                    label = ""

            # Amount display
            try:
                amount = app_state.format_amount(utxo.value, whitespaces=True)
            except Exception:
                amount = ""

            # Build the row item
            utxo_frozen = False
            try:
                utxo_frozen = self._account.is_frozen_utxo(utxo)
            except Exception:
                pass

            utxo_item = SortableTreeWidgetItem([
                prevout_str,
                label,
                amount,
                height_str,
                ""
            ])

            if utxo_frozen:
                utxo_item.setIcon(4, self._frozen_icon)


            # set this here to avoid sorting based on Qt.UserRole+1
            utxo_item.DataRole = Qt.UserRole + 100
            for col in (0, 2):
                utxo_item.setFont(col, self._monospace_font)
            utxo_item.setData(0, Qt.UserRole + 2, utxo)

            # Highlight frozen coins
            try:
                if utxo_frozen:
                    for col in range(self.columnCount()):
                        utxo_item.setBackground(
                            col,
                            ColorScheme.ORANGE.as_color(True)
                        )
            except Exception:
                # Ignore highlighting errors to avoid bailing out of the loop
                pass

            # Add item and restore selection if needed
            self.addChild(utxo_item)
            if utxo in prev_selection:
                utxo_item.setSelected(True)


    def get_selected(self):
        return {item.data(0, Qt.UserRole+2) for item in self.selectedItems()}

    def create_menu(self, position) -> None:
        coins = self.get_selected()
        menu = QMenu()

        # Existing coin-specific actions.
        if coins:

            # Only allow unfrozen UTXOs to be spent.
            spendable_coins = [
                coin for coin in coins
                if not self._account.is_frozen_utxo(coin)
            ]

            if spendable_coins:
                menu.addAction(
                    _("Spend from"),
                    lambda: self._main_window.spend_coins(spendable_coins)
                )
            else:
                # All selected UTXOs are frozen.
                spend_action = menu.addAction(_("Spend from"))
                spend_action.setEnabled(False)

            def freeze_coins() -> None:
                self.freeze_coins(coins, True)

            def unfreeze_coins() -> None:
                self.freeze_coins(coins, False)

            any_c_frozen = any(
                self._account.is_frozen_utxo(coin) for coin in coins
            )
            all_c_frozen = all(
                self._account.is_frozen_utxo(coin) for coin in coins
            )

            if len(coins) == 1:
                # Single selection, offer "Details" and coin freeze status.
                coin = list(coins)[0]
                tx = self._account.get_transaction(coin.tx_hash)

                menu.addAction(
                    _("Details"),
                    lambda: self._main_window.show_transaction(
                        self._account, tx
                    )
                )

                # --- Simple Verification (BEEF Proof) ---
                try:
                    from electrumsv import verification_utils

                    menu.addAction(
                        _("Simple Verify (BREAD)"),
                        lambda: verification_utils.open_simple_verification_window(
                            self._main_window,
                            self._account,
                            tx,
                        )
                    )

                except Exception as e:
                    self._main_window.show_error(
                        f"Verification feature unavailable: {e}"
                    )

                if any_c_frozen:
                    menu.addSeparator()
                    menu.addAction(
                        _("Unfreeze Coin"),
                        unfreeze_coins
                    )
                else:
                    menu.addAction(
                        _("Freeze Coin"),
                        freeze_coins
                    )

            else:
                # Multi-selection.
                menu.addSeparator()

                if not all_c_frozen:
                    menu.addAction(
                        _("Freeze Coins"),
                        freeze_coins
                    )

                if any_c_frozen:
                    menu.addAction(
                        _("Unfreeze Coins"),
                        unfreeze_coins
                    )

        # Provide "Unfreeze All" only when there are frozen UTXOs.
        all_coins = self._account.get_utxos()
        frozen_coins = [
            coin for coin in all_coins
            if self._account.is_frozen_utxo(coin)
        ]

        if frozen_coins:
            menu.addSeparator()

            def unfreeze_all() -> None:
                self.freeze_coins(frozen_coins, False)

            menu.addAction(_("Unfreeze All"), unfreeze_all)

        menu.exec_(self.viewport().mapToGlobal(position))



    def on_permit_edit(self, item, column) -> bool:
        # disable editing fields in this tab (labels)
        return False

    def freeze_coins(self, coins: List[UTXO], freeze: bool) -> None:
        self._main_window.set_frozen_coin_state(self._account, coins, freeze)
