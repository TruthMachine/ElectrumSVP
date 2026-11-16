"""Live hardware state for one window's account; no transaction or signing operations."""
from dataclasses import dataclass, replace

from PyQt5 import sip
from PyQt5.QtCore import QObject, QTimer, pyqtSignal
from PyQt5.QtWidgets import QAction

from electrumsv.app_state import app_state
from electrumsv.i18n import _
from .hardware_icon import hardware_icon


@dataclass(frozen=True)
class AccountKey:
    hw_type: str
    xpub: str
    path: str

    def get_derivation(self):
        return self.path


def availability(rows):
    detected = [row for row in rows if row.device is not None]
    if not detected:
        return 'none'
    return 'available' if any(row.verified_xpubs for row in detected) else 'unavailable'


def merge_probe_results(previous, current):
    """Keep explicit check results only while the same device/app state persists."""
    old = {row.device.id_: row for row in previous if row.device is not None}
    rows = []
    for row in current:
        prior = old.get(row.device.id_) if row.device is not None else None
        unchanged = prior is not None and (prior.state, prior.app, prior.version, prior.device) == \
            (row.state, row.app, row.version, row.device)
        rows.append(replace(row, verified_xpubs=prior.verified_xpubs
            if unchanged and row.state in ('connected', 'bch_ready') else (),
            check_error=prior.check_error if unchanged else ''))
    return tuple(rows)


class HardwareStatusMonitor(QObject):
    changed = pyqtSignal(object)

    def __init__(self, window, action):
        super().__init__(window)
        self.window = window
        self.action = action
        self.rows = ()
        self.stores = ()
        self._generation = 0
        self._inflight = False
        self._paused = False
        self._idle_callbacks = []
        self._timer = QTimer(self)
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self.refresh)
        self.changed.connect(self._update_action)
        window.account_change_signal.connect(self.account_changed)
        self._timer.start()
        self.account_changed(None, getattr(window, '_account', None))

    def account_changed(self, account_id, account):
        self._generation += 1
        app_state.device_manager.hardware.selected_device = None
        self.stores = tuple(AccountKey(store.hw_type, store.xpub, store.get_derivation())
            for store in (account.get_keystores() if account is not None else ())
            if hasattr(store, 'hw_type'))
        # A match for the previous account must never survive an account switch.
        self.rows = tuple(replace(row, verified_xpubs=(), check_error='') for row in self.rows)
        self.changed.emit(self.rows)
        QTimer.singleShot(0, self.refresh)

    def pause(self):
        self._paused = True
        self._generation += 1

    def resume(self):
        self._paused = False

    def when_idle(self, callback):
        # Let an already-running read release USB before an explicit Connect.
        if self._inflight:
            self._idle_callbacks.append(callback)
        else:
            callback()

    def refresh(self):
        if self._paused or self._inflight:
            return
        generation, stores = self._generation, self.stores
        self._inflight = True
        def done(future):
            if sip.isdeleted(self):
                return
            self._inflight = False
            callbacks, self._idle_callbacks = self._idle_callbacks, []
            for callback in callbacks:
                callback()
            if generation != self._generation or self._paused:
                QTimer.singleShot(0, self.refresh)
                return
            try:
                self.rows = merge_probe_results(self.rows, future.result())
            except Exception:
                # A signing operation owns USB exclusively; polling must not
                # interrupt it or turn a busy scan into a false disconnect.
                return
            self.changed.emit(self.rows)
        app_state.app.run_in_thread(
            lambda: app_state.device_manager.hardware.inspect(stores), on_done=done)

    def record_check(self, checked):
        self.rows = tuple(checked if row.device is not None and
            row.device.id_ == checked.device.id_ else row for row in self.rows)
        self.changed.emit(self.rows)

    def _update_action(self, rows):
        state = availability(rows)
        messages = {
            'none': _('No hardware devices detected'),
            'unavailable': _('Hardware device detected, but not available for this account'),
            'available': _('Hardware account key checked; ready to use'),
        }
        text = messages[state]
        self.action.setIcon(hardware_icon(state))
        self.action.setToolTip(text)
        self.action.setStatusTip(text)


def hardware_toolbar_action(window):
    action = getattr(window, '_hardware_action', None)
    if action is None:
        from .hardware import show_hardware_wallets
        action = QAction(hardware_icon('none'), _('Hardware wallet'), window)
        action.triggered.connect(lambda: show_hardware_wallets(window))
        window._hardware_action = action
        window.add_toolbar_action(action)
        window._hardware_monitor = HardwareStatusMonitor(window, action)
    return action
