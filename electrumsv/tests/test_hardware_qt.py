"""Qt account flow and thread ownership, with simulated device I/O."""
from concurrent.futures import Future, ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock
import threading

import pytest
pytest.importorskip('PyQt5')
from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import QApplication, QWidget

from electrumsv.app_state import AppStateProxy, app_state
from electrumsv.simple_config import SimpleConfig
from electrumsv.exceptions import UserCancelled
from electrumsv.gui.qt import account_wizard as aw
from electrumsv.gui.qt import hardware_accounts as ha
from electrumsv.gui.qt.hardware import run_device_operation


@pytest.fixture
def gui(tmp_path):
    app = QApplication.instance() or QApplication([])
    previous = app_state._proxy
    AppStateProxy(SimpleConfig({'electrum_sv_path': str(tmp_path)}), 'qt')
    yield app
    app_state.set_proxy(previous)


def test_hardware_account_pages_are_reachable(gui):
    window = QWidget()
    window._wallet = Mock()
    wizard = aw.AccountWizard(window)
    assert wizard.page(aw.AccountPage.FIND_HARDWARE_WALLET) is not None
    assert wizard.page(aw.AccountPage.SETUP_HARDWARE_WALLET) is not None
    assert aw.ResultType.HARDWARE.value == 4


def test_device_work_runs_off_gui_thread_and_returns_on_gui_thread(gui):
    class Bridge(QObject):
        done = pyqtSignal(object)
    bridge = Bridge()
    gui_thread = threading.get_ident()
    with ThreadPoolExecutor(max_workers=1) as executor:
        def run(func, on_done):
            bridge.done.connect(on_done)
            future = executor.submit(func)
            future.add_done_callback(bridge.done.emit)
            return future
        app_state.app = SimpleNamespace(run_in_thread=run)
        parent = QWidget()
        worker_thread = run_device_operation(parent, threading.get_ident)
    assert worker_thread != gui_thread


def test_cancelled_setup_cannot_create_an_account(gui, monkeypatch):
    window = QWidget()
    window._wallet = Mock()
    wizard = aw.AccountWizard(window)
    wizard.set_selected_device(('ledger', SimpleNamespace(device=SimpleNamespace(id_='test'))))
    support = Mock()
    support.connect.side_effect = UserCancelled()
    app_state.device_manager.hardware = support
    monkeypatch.setattr(ha, 'run_device_operation', lambda parent, task: task())
    page = wizard.page(aw.AccountPage.SETUP_HARDWARE_WALLET)
    page._initiate_setup()
    assert not page.isComplete()
    assert page._plugin is None
    assert 'cancelled' in page._plugin_debug_message
    window._wallet.create_account_from_keystore.assert_not_called()


def test_scan_completion_ignores_departed_page(gui):
    window = QWidget()
    window._wallet = Mock()
    wizard = aw.AccountWizard(window)
    page = wizard.page(aw.AccountPage.FIND_HARDWARE_WALLET)
    page._alive = False
    future = Future()
    future.set_exception(OSError('device unplugged'))
    page._on_scan_complete(future, page._scan_generation)
    assert not page.isComplete()


def test_old_scan_cannot_replace_results_after_reentering_page(gui):
    window = QWidget()
    window._wallet = Mock()
    wizard = aw.AccountWizard(window)
    page = wizard.page(aw.AccountPage.FIND_HARDWARE_WALLET)
    page._alive = True
    page._scan_generation = 2
    page._devices = ['current scan']
    future = Future()
    future.set_result(SimpleNamespace(devices=['old scan'], errors={}))
    page._on_scan_complete(future, 1)
    assert page._devices == ['current scan']


def test_input_selection_never_offers_already_signed_or_foreign_inputs(gui):
    from PyQt5.QtCore import Qt
    from electrumsv.tests.test_external_signing import transaction, CAPABILITIES, sign
    from electrumsv.devices.signing import prepare_signing_request, apply_signing_result
    from electrumsv.transaction import TransactionContext
    from electrumsv.gui.qt.hardware import SigningInputsDialog
    tx, keys = transaction()
    store = SimpleNamespace(is_signature_candidate=lambda key: key.to_public_key() == keys[1].public_key)
    parent = QWidget()
    dialog = SigningInputsDialog(parent, tx, [store])
    assert dialog.selected_indexes() == {1}
    assert dialog.inputs.topLevelItemCount() == 1
    dialog.inputs.topLevelItem(0).setCheckState(0, Qt.Unchecked)
    assert not dialog.selected_indexes()
    assert not dialog.sign_button.isEnabled()
    request = prepare_signing_request(tx, TransactionContext(), store.is_signature_candidate, CAPABILITIES)
    apply_signing_result(tx, request, sign(request, keys[1]))
    finished = SigningInputsDialog(parent, tx, [store])
    assert finished.inputs.topLevelItemCount() == 0


def test_message_hash_preserves_spaces_newlines_and_unicode(gui):
    from PyQt5.QtWidgets import QTextEdit
    from electrumsv.gui.qt.hardware import MessageHashReview
    import hashlib
    edit = QTextEdit()
    review = MessageHashReview(edit)
    for text in [' a\nb ', '矿工签名\n', '']:
        edit.setPlainText(text)
        assert review.digest.text() == hashlib.sha256(text.encode('utf8')).hexdigest().upper()


def test_polling_preserves_connection_result_until_device_state_changes():
    from dataclasses import replace
    from electrumsv.devices.hardware import DeviceStatus
    from electrumsv.gui.qt.hardware_status import merge_probe_results
    from electrumsv.gui.qt.hardware import device_status_text
    from electrumsv.i18n import _
    row = DeviceStatus('ledger', 'Nano X', 'bch_ready', 'Ready',
        app='Bitcoin Cash', version='2.4.14', device=SimpleNamespace(id_='x'))
    failed = replace(row, check_error='Account key does not match')
    assert merge_probe_results((failed,), (row,))[0].check_error == failed.check_error
    passed = replace(row, verified_xpubs=('checked-xpub',))
    assert merge_probe_results((passed,), (row,))[0].verified_xpubs == ('checked-xpub',)
    locked = replace(row, state='locked', detail='Unlock', app='', version='')
    result = merge_probe_results((passed,), (locked,))[0]
    assert not result.verified_xpubs and not result.check_error
    assert device_status_text(result) == _('Locked')
    assert not merge_probe_results((result,), (row,))[0].verified_xpubs
    assert merge_probe_results((passed,), ()) == ()


def test_probe_updates_rows_without_replacing_selection_or_focus(gui):
    from dataclasses import replace
    from electrumsv.devices.hardware import DeviceStatus
    from electrumsv.gui.qt.hardware import HardwareWalletDialog
    parent = QWidget(); dialog = HardwareWalletDialog(parent)
    rows = [DeviceStatus('ledger', 'Nano {}'.format(i), 'locked', 'Unlock',
        device=SimpleNamespace(id_=str(i))) for i in range(25)]
    dialog.show(); dialog.show_rows(rows); gui.processEvents()
    selected = dialog.devices.topLevelItem(15)
    dialog.devices.setCurrentItem(selected)
    dialog.devices.setFocus(); dialog.devices.scrollToItem(selected)
    gui.processEvents()
    position = dialog.devices.verticalScrollBar().value()
    focus = gui.focusWidget()
    updated = list(rows); updated[15] = replace(rows[15], state='bch_ready',
        app='Bitcoin Cash', version='2.4.14')
    dialog.show_rows(updated); gui.processEvents()
    assert dialog.devices.currentItem() is selected
    assert gui.focusWidget() is focus
    assert dialog.devices.verticalScrollBar().value() == position
    assert not dialog.select_button.isEnabled()
    dialog.show_rows(updated[:15] + updated[16:]); gui.processEvents()
    assert dialog.devices.currentItem() is None
    assert not dialog.connect_button.isEnabled() and not dialog.select_button.isEnabled()
    dialog.reject(); parent.close()


def test_candidates_require_connection_check_before_selection(gui, monkeypatch):
    from dataclasses import replace
    from PyQt5.QtCore import Qt
    from electrumsv.devices.hardware import DeviceStatus
    from electrumsv.gui.qt import hardware
    parent = QWidget()
    store = SimpleNamespace(hw_type='ledger', xpub='test-xpub', get_derivation=lambda: "m/44'/0'/1'")
    support = Mock(selected_device=None)
    nano = DeviceStatus('ledger', 'Nano X', 'bch_ready', 'BCH ready',
        app='Bitcoin Cash', version='2.4.14', device=SimpleNamespace(id_='x'))
    support.inspect.return_value = [DeviceStatus('ledger', 'Nano S', 'locked', 'Unlock Nano S',
        device=SimpleNamespace(id_='s')), nano,
        DeviceStatus('trezor', 'Trezor', 'not_detected', 'Not connected'),
        DeviceStatus('keepkey', 'KeepKey', 'connected', 'Connected', device=SimpleNamespace(id_='k'))]
    support.check_device.return_value = replace(nano, verified_xpubs=('test-xpub',))
    def run(task, on_done):
        future = Future()
        try:
            future.set_result(task())
        except Exception as exc:
            future.set_exception(exc)
        on_done(future)
    app_state.app = SimpleNamespace(run_in_thread=run)

    app_state.device_manager.hardware = support
    monkeypatch.setattr(hardware, 'run_device_operation', lambda parent, task: task())
    dialog = hardware.HardwareWalletDialog(parent, [store])
    dialog.refresh()
    dialog.show()
    gui.processEvents()
    assert dialog.devices.topLevelItemCount() == 3  # Detected only, regardless of account brand.
    assert dialog.devices.currentItem() is None
    assert not dialog.select_button.isEnabled()
    assert not dialog.connect_button.isEnabled()
    item = dialog.devices.topLevelItem(1)
    dialog.devices.setCurrentItem(item)
    assert dialog.connect_button.isEnabled()
    assert not dialog.select_button.isEnabled()
    assert item.checkState(0) == Qt.Unchecked
    dialog.connect_selected()
    assert support.check_device.call_args.args[0].device.id_ == 'x'
    assert support.check_device.call_args.args[1] == (store,)
    assert item.checkState(0) == Qt.Checked
    assert dialog.select_button.isEnabled()
    assert support.selected_device is None  # Connect does not commit a transport choice.
    dialog.select_checked()
    assert support.selected_device.device.id_ == 'x'
    dialog = hardware.HardwareWalletDialog(parent, [store])
    dialog.refresh()
    dialog.devices.setCurrentItem(dialog.devices.topLevelItem(0))
    support.check_device.side_effect = UserCancelled()
    dialog.connect_selected()
    assert not dialog.select_button.isEnabled()
    assert 'cancelled' in dialog.details.text()
    dialog.close()


def test_live_status_drops_old_account_match_and_ignores_late_scan(gui):
    from dataclasses import replace
    from PyQt5.QtWidgets import QAction
    from electrumsv.devices.hardware import DeviceStatus
    from electrumsv.gui.qt.hardware_status import HardwareStatusMonitor, availability
    class Window(QWidget):
        account_change_signal = pyqtSignal(object, object)
    window = Window()
    jobs = []
    app_state.app = SimpleNamespace(run_in_thread=lambda task, on_done: jobs.append((task, on_done)))
    monitor = HardwareStatusMonitor(window, QAction(window))
    gui.processEvents()
    row = DeviceStatus('ledger', 'Nano X', 'bch_ready', '', device=SimpleNamespace(id_='x'),
        verified_xpubs=('old-key',))
    monitor.rows = (row,)
    assert availability(monitor.rows) == 'available'
    new_store = SimpleNamespace(hw_type='ledger', xpub='new-key', get_derivation=lambda: "m/44'/0'/1'")
    monitor.account_changed(2, SimpleNamespace(get_keystores=lambda: [new_store]))
    assert availability(monitor.rows) == 'unavailable'
    future = Future(); future.set_result((row,))
    jobs[0][1](future)
    assert not monitor.rows[0].verified_xpubs  # Late old-account read cannot restore green.
    assert availability(()) == 'none'
    assert availability((replace(row, state='locked', verified_xpubs=()),)) == 'unavailable'
    monitor._timer.stop()
    monitor.pause()
    window.close()


def test_explicit_connect_waits_for_background_scan_to_release_usb(gui):
    from PyQt5.QtWidgets import QAction
    from electrumsv.gui.qt.hardware_status import HardwareStatusMonitor
    class Window(QWidget):
        account_change_signal = pyqtSignal(object, object)
    window, jobs, started = Window(), [], []
    app_state.app = SimpleNamespace(run_in_thread=lambda task, on_done: jobs.append((task, on_done)))
    monitor = HardwareStatusMonitor(window, QAction(window))
    gui.processEvents()
    monitor.pause()
    monitor.when_idle(lambda: started.append(True))
    assert not started
    future = Future(); future.set_result(())
    jobs[0][1](future)
    assert started == [True]
    monitor._timer.stop()
    window.close()


def test_cancelled_password_restores_transaction_signing_controls(gui):
    from electrumsv.gui.qt.transaction_dialog import TxDialog
    from electrumsv.tests.test_external_signing import transaction
    tx, _ = transaction()
    window = Mock()
    window.sign_tx.return_value = None  # @protected exits before starting a worker.
    dialog = SimpleNamespace(tx=tx, _payment_request=None, _main_window=window,
        sign_button=Mock(), partial_sign_button=Mock(), update=Mock())
    TxDialog.sign(dialog, input_indexes={1})
    window.sign_tx.assert_called_once()
    assert window.sign_tx.call_args.kwargs['input_indexes'] == {1}
    dialog.update.assert_called_once()
    window.pop_top_level_window.assert_called_once_with(dialog)


def test_parent_verification_choice_is_reset_for_each_signing_operation(gui):
    from electrumsv.gui.qt.hardware import LedgerSigningOptionsDialog
    from electrumsv.tests.test_external_signing import transaction
    tx, _ = transaction()
    parent = QWidget()
    first = LedgerSigningOptionsDialog(parent, tx)
    assert first.verify_inputs.isChecked()
    first.verify_inputs.setChecked(False)
    first.accept()
    second = LedgerSigningOptionsDialog(parent, tx)
    assert second.verify_inputs.isChecked()
    assert not first.verify_inputs.isChecked()


def test_many_input_progress_updates_reuse_one_ledger_dialog(gui):
    from electrumsv.devices.ledger.qt import Ledger_Handler
    parent = QWidget()
    parent.top_level_window = lambda: parent
    handler = Ledger_Handler(parent)
    handler.message_dialog('Verifying input 1 of 512')
    dialog = handler.dialog
    handler.message_dialog('Verifying input 512 of 512')
    assert handler.dialog is dialog
    assert handler._message_label.text() == 'Verifying input 512 of 512'
    handler.progress_dialog('Signing input 256 of 512', 256, 512)
    assert handler._progress.maximum() == 512
    assert handler._progress.value() == 255
    assert handler._operation_timer.isActive()
    assert '00:00:' in handler._elapsed_label.text()
    handler.clear_dialog()
    assert not handler._operation_timer.isActive()


def test_picker_can_close_during_connection_without_selecting_late_result(gui):
    from dataclasses import replace
    from PyQt5.QtCore import Qt
    from electrumsv.devices.hardware import DeviceStatus
    from electrumsv.gui.qt.hardware import HardwareWalletDialog
    pending = []
    app_state.app = SimpleNamespace(run_in_thread=lambda task, on_done: pending.append(on_done))
    support = Mock(selected_device=None)
    app_state.device_manager.hardware = support
    parent = QWidget()
    dialog = HardwareWalletDialog(parent)
    row = DeviceStatus('ledger', 'Nano X', 'bch_ready', '', device=SimpleNamespace(id_='x'))
    dialog.show_rows([row]); dialog.show()
    dialog.devices.setCurrentItem(dialog.devices.topLevelItem(0))
    dialog.connect_selected()
    assert dialog.windowModality() == Qt.NonModal
    dialog.reject()
    assert not dialog.isVisible()
    future = Future(); future.set_result(replace(row, verified_xpubs=('key',)))
    pending[0](future)
    assert support.selected_device is None
    assert not dialog.select_button.isEnabled()


@pytest.mark.parametrize('points', [13, 18])
def test_signing_options_fit_wrapped_warning_and_default_to_continue(gui, points):
    from electrumsv.gui.qt.hardware import LedgerSigningOptionsDialog
    from electrumsv.tests.test_external_signing import transaction
    from PyQt5.QtWidgets import QPushButton
    parent = QWidget(); dialog = LedgerSigningOptionsDialog(parent, transaction()[0])
    font = dialog.font(); font.setPointSize(points); dialog.setFont(font)
    dialog.show(); gui.processEvents()
    for enabled in (True, False):
        dialog.verify_inputs.setChecked(enabled)
        dialog.resize(500, 200); gui.processEvents()
        assert dialog.note.height() >= dialog.note.heightForWidth(dialog.note.width())
        assert dialog.note.geometry().bottom() < dialog.proceed_button.geometry().top()
        assert dialog.proceed_button.isDefault()
        assert all(not b.isDefault() for b in dialog.findChildren(QPushButton)
            if b is not dialog.proceed_button)
    dialog.reject()
