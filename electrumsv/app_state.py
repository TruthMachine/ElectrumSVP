# ElectrumSV - lightweight Bitcoin SV client
# Copyright (C) 2019-2020 The ElectrumSV Developers
# Copyright (C) 2012 thomasv@gitorious
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

import os
import time
import shutil
import logging
from typing import Optional, Tuple

from bitcoinx import Bitcoin
from bitcoinx.errors import MissingHeader

from .async_ import ASync
from .constants import MAX_INCOMING_ELECTRUMX_MESSAGE_MB
from .logs import logs
from .simple_config import SimpleConfig
from .util import format_satoshis
from .headers_storage import PersistentHeaders

logger = logs.get_logger("app_state")


class DefaultApp:
    def __init__(self):
        pass

    def run_app(self):
        global app_state
        while app_state.daemon.is_running():
            time.sleep(0.5)

    def setup_app(self):
        # Initialise things dependent upon app_state.daemon here
        return

    def on_new_wallet_event(self, wallet_path, row) -> None:
        # Expected API when resetting/creating a new wallet
        pass


class AppStateProxy:
    app = None
    base_unit_ids = ['Bitcoin', 'BSV', 'BSVblockchain tokens', 'mBSV', 'millibitcoin', 'kilobits', 'bits', 'sats']  # unique identifiers 
    base_units = ['Bitcoin', 'BSV', 'BSVblockchain tokens', 'mBSV', 'millibitcoin', 'kilobits', 'bits', 'sats']  # display names
    decimal_points = [8, 8, 8, 5, 5, 5, 2, 0]                        # decimal precision

    def __init__(self, config: SimpleConfig, gui_kind: str) -> None:
        from electrumsv.device import DeviceMgr
        self.config = config
        self.gui_kind = gui_kind

        # Set self as the global proxy
        AppState.set_proxy(self)

        self.device_manager = DeviceMgr()
        self.fx = None
        self.headers: Optional[PersistentHeaders] = None

        # Load and validate base_unit_index (default to 0 / Bitcoin).
        base_unit_index = config.get('base_unit_index', 0)
        try:
            base_unit_index = int(base_unit_index)
        except Exception:
            base_unit_index = 0
        if not (0 <= base_unit_index < len(self.base_units)):
            base_unit_index = 0
        self.base_unit_index = base_unit_index

        # Ensure the base_unit_index is present in config (persist default on first run)
        if config.get('base_unit_index', None) is None:
            self.config.set_key('base_unit_index', self.base_unit_index, True)

        # Determine expected decimal precision for the selected base unit.
        expected_decimal = self.decimal_points[self.base_unit_index]

        # Load stored decimal_point and validate; if missing or inconsistent, overwrite
        stored_decimal = config.get('decimal_point', None)
        if stored_decimal is None:
            self.decimal_point = expected_decimal
            self.config.set_key('decimal_point', self.decimal_point, True)
        else:
            try:
                stored_decimal = int(stored_decimal)
            except Exception:
                stored_decimal = expected_decimal
            # If stored value doesn't match the semantics of the selected unit, normalize it.
            if stored_decimal != expected_decimal:
                self.decimal_point = expected_decimal
                self.config.set_key('decimal_point', self.decimal_point, True)
            else:
                self.decimal_point = stored_decimal

        # Preserve num_zeros if present (unchanged behaviour)
        self.num_zeros = config.get('num_zeros', 0)

        # Async helper
        self.async_ = ASync()


    def has_app(self):
        return self.app is not None

    def set_app(self, app) -> None:
        self.app = app

    def headers_filename(self) -> str:
        return os.path.join(self.config.path, 'headers')

    def read_headers(self):
        filename = self.config.file_path('headers')

        if not os.path.exists(filename):
            logger.info("No headers file found, copying preloaded headers to %s", filename)
            os.makedirs(os.path.dirname(filename), exist_ok=True)
            shutil.copyfile(PRELOADED_HEADERS, filename)

        try:
            self.headers = PersistentHeaders(file_path=filename, network=Bitcoin)
        except MissingHeader as e:
            logger.warning("Existing headers file failed: %s", str(e))
            old_backup = filename + ".old"
            if os.path.exists(filename):
                os.rename(filename, old_backup)
                logger.info("Renamed old headers file to %s", old_backup)
            shutil.copyfile(PRELOADED_HEADERS, filename)
            logger.info("Copied preloaded headers to %s", filename)
            self.headers = PersistentHeaders(file_path=filename, network=Bitcoin)

    async def on_stop(self) -> None:
        if hasattr(self, '_poll_task') and self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        if self.headers is not None:
            logger.debug("Flushing headers to disk")
            try:
                self.headers.flush()
            except Exception as e:
                logger.exception(f"Error while flushing headers: {e}")

    # Amount formatting
    def base_unit(self) -> str:
        return self.base_units[self.base_unit_index]


    def set_base_unit(self, unit_name: str) -> bool:
        if unit_name not in self.base_units:
            return False
        new_index = self.base_units.index(unit_name)
        if new_index != self.base_unit_index:
            self.base_unit_index = new_index
            self.config.set_key('base_unit_index', self.base_unit_index, True)
            self.decimal_point = self.decimal_points[self.base_unit_index]
            return True
        return False


    def format_amount(self, x: Optional[int], is_diff: bool=False, whitespaces: bool=False) -> str:
        return format_satoshis(x, self.num_zeros, self.decimal_point, is_diff=is_diff, whitespaces=whitespaces)

    def format_amount_and_units(self, amount: Optional[int]) -> str:
        text = self.format_amount(amount) + ' ' + self.base_unit()
        if self.fx and self.fx.is_enabled():
            fx_text = self.fx.format_amount_and_units(amount)
            if text and fx_text:
                text += f' ({fx_text})'
        return text

    def get_amount_and_units(self, amount: int) -> Tuple[str, str]:
        bitcoin_text = self.format_amount(amount) + ' ' + self.base_unit()
        fiat_text = self.fx.format_amount_and_units(amount) if self.fx and self.fx.is_enabled() else ''
        return bitcoin_text, fiat_text

    def electrumx_message_size_limit(self) -> int:
        return max(0, self.config.get('electrumx_message_size_limit', MAX_INCOMING_ELECTRUMX_MESSAGE_MB))

    def set_electrumx_message_size_limit(self, maximum_size: int) -> None:
        assert maximum_size >= 0, f"invalid cache size {maximum_size}"
        self.config.set_key('electrumx_message_size_limit', maximum_size)


class _AppStateMeta(type):
    def __getattr__(cls, attr):
        return getattr(cls._proxy, attr)

    def __setattr__(cls, attr, value):
        if attr == '_proxy':
            super().__setattr__(attr, value)
        else:
            setattr(cls._proxy, attr, value)


class AppState(metaclass=_AppStateMeta):
    _proxy = None

    @classmethod
    def set_proxy(cls, proxy):
        cls._proxy = proxy


app_state = AppState

