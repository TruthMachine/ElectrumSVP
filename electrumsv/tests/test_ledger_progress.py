"""Signing uses one window, with explicit phase events and cancellation boundaries."""
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
pytest.importorskip('PyQt5')
from PyQt5.QtWidgets import QApplication, QWidget
from electrumsv.app_state import AppStateProxy, app_state
from electrumsv.simple_config import SimpleConfig
from electrumsv.exceptions import UserCancelled
from electrumsv.gui.qt.ledger_progress import LedgerSigningDialog
from electrumsv.devices.ledger.qt import Ledger_Handler
from electrumsv.tests.test_external_signing import transaction


@pytest.fixture
def flow(tmp_path):
    app = QApplication.instance() or QApplication([])
    previous = app_state._proxy
    AppStateProxy(SimpleConfig({'electrum_sv_path': str(tmp_path)}), 'qt')
    pending = []
    app_state.app = SimpleNamespace(run_in_thread=lambda func, on_done:
        pending.append((func, on_done)))
    parent = QWidget(); parent.top_level_window = lambda: parent
    handler = Ledger_Handler(parent)
    callback = Mock(); operation = Mock()
    dialog = LedgerSigningDialog(parent, transaction()[0], [handler], operation, callback)
    dialog.show(); app.processEvents()
    yield app, dialog, handler, pending, operation, callback
    if dialog.running:
        result = Future(); result.set_exception(UserCancelled()); dialog._done(result)
    dialog._finish(); parent.close()
    app_state.set_proxy(previous)


def test_progress_and_questions_reuse_the_options_window(flow):
    app, dialog, handler, pending, operation, callback = flow
    initial_size = dialog.size()
    dialog._proceed()
    handler.message_dialog('Check the device')
    handler.stage_dialog('parents', 3, 4)
    handler.progress_dialog('Verifying input 4 of 4', 4, 4)
    assert handler.dialog is None
    assert dialog._bars['parents'].value() == 3
    assert dialog._bars['prepare'].value() == 1
    handler.win_yes_no_question('Continue with reduced verification?')
    assert dialog.proceed_button.isEnabled()
    dialog._proceed()
    assert handler.done.is_set() and handler._ok == 1
    handler.clear_dialog()
    assert dialog.isVisible()
    handler.stage_dialog('review', 0, 0)
    handler.stage_dialog('sign', 1, 2)
    assert dialog._bars['review'].value() == 1
    assert dialog._bars['sign'].value() == 1
    assert dialog.size() == initial_size
    callback.assert_not_called()


def test_cancel_does_not_abandon_running_device_request(flow):
    app, dialog, handler, pending, operation, callback = flow
    dialog._proceed(); dialog.close(); app.processEvents()
    assert dialog.isVisible() and dialog.cancel_event.is_set()
    with pytest.raises(UserCancelled): handler.check_cancelled()
    callback.assert_not_called()
    future = Future(); future.set_exception(UserCancelled())
    pending[0][1](future)
    assert handler._signing_dialog is None
    assert dialog.isVisible() and not dialog.running
    dialog.reject()
    callback.assert_called_once_with(False)
    assert not dialog.isVisible()


def test_completion_waits_for_host_result_and_closes_before_callback(flow):
    app, dialog, handler, pending, operation, callback = flow
    dialog._proceed(); handler.stage_dialog('parents', 0, -1)
    handler.stage_dialog('review', 0, 0); handler.stage_dialog('sign', 2, 2)
    handler.finished(); app.processEvents()
    assert dialog.running and dialog.isVisible()
    handler.stage_dialog('verify', 0, 0)
    future = Future(); future.set_result(None); pending[0][1](future)
    assert dialog._bars['parents'].format() == 'Skipped'
    assert all(bar.value() == 1 for stage, bar in dialog._bars.items() if stage != 'parents')
    dialog._finish(); callback.assert_called_once_with(True)
    assert not dialog.isVisible()


def test_cancel_pending_question_releases_worker(flow):
    app, dialog, handler, pending, operation, callback = flow
    dialog._proceed(); handler.win_yes_no_question('Continue?')
    dialog.reject()
    assert handler.done.is_set() and handler._ok == 0
