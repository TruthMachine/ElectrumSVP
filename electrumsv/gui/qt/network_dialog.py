#!/usr/bin/env python
#
# ElectrumSV - lightweight Bitcoin SV client
# Adapted network dialog with TAILS auto-proxy support
#

import socket
from PyQt5.QtCore import pyqtSignal, Qt, QThread
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QTreeWidget, QMenu, QTreeWidgetItem, QHeaderView, QTabWidget,
    QWidget, QGridLayout, QLineEdit, QCheckBox, QLabel, QComboBox, QSizePolicy
)

from aiorpcx import NetAddress
from bitcoinx import hash_to_hex_str

from electrumsv.app_state import app_state
from electrumsv.i18n import _
from electrumsv.logs import logs
from electrumsv.network import Network, SVServer, SVProxy, SVUserAuth
from electrumsv.networks import Net

from .password_dialog import PasswordLineEdit
from .util import (Buttons, CloseButton, FormSectionWidget, HelpButton, HelpDialogButton,
    read_QIcon, MessageBox)

logger = logs.get_logger("networkui")

def is_tails_os() -> bool:
    try:
        with open("/etc/os-release", "r") as f:
            return "tails" in f.read().lower()
    except Exception:
        return False


class NetworkDialog(QDialog):
    network_updated_signal = pyqtSignal()

    def __init__(self, network: Network, config) -> None:
        super().__init__(flags=Qt.WindowSystemMenuHint | Qt.WindowTitleHint |
            Qt.WindowCloseButtonHint)
        self.setWindowTitle(_('Network'))
        self.setMinimumSize(500, 200)
        self.resize(560, 400)
        self._nlayout = NetworkChoiceLayout(network, config)

        buttons_layout = Buttons(CloseButton(self))
        buttons_layout.add_left_button(HelpDialogButton(self, "misc", "network-dialog"))

        vbox = QVBoxLayout(self)
        vbox.setSizeConstraint(QVBoxLayout.SetFixedSize)
        vbox.addLayout(self._nlayout.layout())
        vbox.addLayout(buttons_layout)

        self.network_updated_signal.connect(self.on_update)
        network.register_callback(self.on_network, ['updated', 'sessions'])

    def on_network(self, event, *args):
        ''' This may run in network thread '''
        self.network_updated_signal.emit()

    def on_update(self):
        ''' This always runs in main GUI thread '''
        self._nlayout.update()


class NodesListWidget(QTreeWidget):

    def __init__(self, parent):
        super().__init__()
        self.parent = parent
        self.setHeaderLabels([_('Connected node'), _('Height')])
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.create_menu)

    def create_menu(self, position):
        item = self.currentItem()
        if not item:
            return
        server = item.data(0, Qt.UserRole)
        if not server:
            return

        def use_as_server():
            self.parent.follow_server(server)
        menu = QMenu()
        menu.addAction(_("Use as server"), use_as_server)
        menu.exec_(self.viewport().mapToGlobal(position))

    def keyPressEvent(self, event):
        if event.key() in [Qt.Key_F2, Qt.Key_Return]:
            self.on_activated(self.currentItem(), self.currentColumn())
        else:
            super().keyPressEvent(event)

    def on_activated(self, item, column):
        pt = self.visualItemRect(item).bottomLeft()
        pt.setX(50)
        self.customContextMenuRequested.emit(pt)

    def chain_name(self, chain, our_chain):
        if chain is our_chain:
            return 'our_chain'
        _chain, common_height = our_chain.common_chain_and_height(chain)
        fork_height = common_height + 1
        headers_obj = app_state.headers
        header = headers_obj.header_at_height(chain, fork_height)
        prefix = hash_to_hex_str(header.hash).lstrip('00')[0:10]
        return f'{prefix}@{fork_height}'

    def update(self, network):
        self.clear()
        self.addChild = self.addTopLevelItem
        chains = network.sessions_by_chain()
        our_chain = network.chain()
        for chain, sessions in chains.items():
            if len(chains) > 1:
                name = self.chain_name(chain, our_chain)
                x = QTreeWidgetItem([name, '%d' % chain.height])
                x.setData(0, Qt.UserRole, None)
            else:
                x = self
            host_counts = {}
            for session in sessions:
                host_counts[session.server.host] = host_counts.get(session.server.host, 0) + 1
            for session in sessions:
                extra_name = ""
                if host_counts[session.server.host] > 1:
                    extra_name = f" (port: {session.server.port})"
                extra_name += ' (main server)' if session.server is network.main_server else ''
                item = QTreeWidgetItem([session.server.host + extra_name, str(session.tip.height)])
                item.setData(0, Qt.UserRole, session.server)
                x.addChild(item)
            if len(chains) > 1:
                self.addTopLevelItem(x)
                x.setExpanded(True)

        h = self.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)


class ServerListWidget(QTreeWidget):

    def __init__(self, parent):
        super().__init__()
        self.parent = parent
        self.setHeaderLabels([_('Host'), _('Protocol'), _('Port')])
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.create_menu)

    def create_menu(self, position):
        item = self.currentItem()
        if not item:
            return
        server = item.data(0, Qt.UserRole)
        if not server:
            return
        menu = QMenu()
        menu.addAction(_("Use as server"), lambda: self.set_server(server))
        menu.exec_(self.viewport().mapToGlobal(position))

    def set_server(self, server):
        self.parent.server_host.setText(server.host)
        self.parent.server_port.setText(str(server.port))
        self.parent.set_server(server)

    def keyPressEvent(self, event):
        if event.key() in [Qt.Key_F2, Qt.Key_Return]:
            self.on_activated(self.currentItem(), self.currentColumn())
        else:
            super().keyPressEvent(event)

    def on_activated(self, item, column):
        pt = self.visualItemRect(item).bottomLeft()
        pt.setX(50)
        self.customContextMenuRequested.emit(pt)

    def update(self, servers, protocol, use_tor):
        self.clear()
        for server in servers:
            if server.host.endswith('.onion') and not use_tor:
                continue
            x = QTreeWidgetItem([server.host, server.protocol_text(), str(server.port)])
            x.setData(0, Qt.UserRole, server)
            self.addTopLevelItem(x)
        h = self.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)


class NetworkChoiceLayout(object):

    def __init__(self, network, config, wizard=False):
        self.network = network
        self.config = config
        self.protocol = None
        self.tor_proxy = None
        self.filling_in = False

        self.tabs = tabs = QTabWidget()
        tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        server_tab = QWidget()
        proxy_tab = QWidget()
        blockchain_tab = QWidget()
        tabs.addTab(blockchain_tab, _('Overview'))
        tabs.addTab(server_tab, _('Server'))
        tabs.addTab(proxy_tab, _('Proxy'))

        if wizard:
            tabs.setCurrentIndex(1)

        # Server tab
        grid = QGridLayout(server_tab)
        grid.setSpacing(8)

        self.server_host = QLineEdit()
        self.server_host.setFixedWidth(200)
        self.server_port = QLineEdit()
        self.server_port.setFixedWidth(60)
        self.autoconnect_cb = QCheckBox(_('Select server automatically'))
        self.autoconnect_cb.setEnabled(self.config.is_modifiable('auto_connect'))

        self.server_host.editingFinished.connect(self.set_server)
        self.server_port.editingFinished.connect(self.set_server)
        self.autoconnect_cb.clicked.connect(self._on_autoconnect_toggled)
        self.autoconnect_cb.clicked.connect(self.update)

        msg = ' '.join([
            _("If auto-connect is enabled, ElectrumSV will always use a server that "
              "is on the longest blockchain."),
            _("If it is disabled, you have to choose a server you want to use. "
              "ElectrumSV will warn you if your server is lagging.")
        ])
        grid.addWidget(self.autoconnect_cb, 0, 0, 1, 3)
        grid.addWidget(HelpButton(msg), 0, 4)

        grid.addWidget(QLabel(_('Server') + ':'), 1, 0)
        grid.addWidget(self.server_host, 1, 1, 1, 2)
        grid.addWidget(self.server_port, 1, 3)

        label = _('Server peers') if network.is_connected() else _('Default Servers')
        grid.addWidget(QLabel(label), 2, 0, 1, 5)
        self.servers_list = ServerListWidget(self)
        grid.addWidget(self.servers_list, 3, 0, 1, 5)

        # Proxy tab
        grid = QGridLayout(proxy_tab)
        grid.setSpacing(8)

        self.proxy_cb = QCheckBox(_('Use proxy'))
        self.proxy_cb.clicked.connect(self.check_disable_proxy)
        self.proxy_cb.clicked.connect(self.set_proxy)

        self.proxy_mode = QComboBox()
        self.proxy_mode.addItems(list(SVProxy.kinds))
        self.proxy_host = QLineEdit()
        self.proxy_host.setFixedWidth(200)
        self.proxy_port = QLineEdit()
        self.proxy_port.setFixedWidth(100)
        self.proxy_username = QLineEdit()
        self.proxy_username.setPlaceholderText(_("Proxy user"))
        self.proxy_username.setFixedWidth(self.proxy_host.width())
        self.proxy_password = PasswordLineEdit()
        self.proxy_password.setPlaceholderText(_("Password"))

        self.proxy_mode.currentIndexChanged.connect(self.set_proxy)
        self.proxy_host.editingFinished.connect(self.set_proxy)
        self.proxy_port.editingFinished.connect(self.set_proxy)
        self.proxy_username.editingFinished.connect(self.set_proxy)
        self.proxy_password.editingFinished.connect(self.set_proxy)

        self.proxy_mode.currentIndexChanged.connect(self.proxy_settings_changed)
        self.proxy_host.textEdited.connect(self.proxy_settings_changed)
        self.proxy_port.textEdited.connect(self.proxy_settings_changed)
        self.proxy_username.textEdited.connect(self.proxy_settings_changed)
        self.proxy_password.textEdited.connect(self.proxy_settings_changed)

        self.tor_cb = QCheckBox(_("Use Tor Proxy"))
        self.tor_cb.setIcon(read_QIcon("tor_logo.png"))
        self.tor_cb.hide()
        self.tor_cb.clicked.connect(self.use_tor_proxy)

        grid.addWidget(self.tor_cb, 1, 0, 1, 3)
        grid.addWidget(self.proxy_cb, 2, 0, 1, 3)
        grid.addWidget(HelpButton(_('Proxy settings apply to all connections: both '
                                    'ElectrumSV servers and third-party services.')), 2, 4)
        grid.addWidget(self.proxy_mode, 4, 1)
        grid.addWidget(self.proxy_host, 4, 2)
        grid.addWidget(self.proxy_port, 4, 3)
        grid.addWidget(self.proxy_username, 5, 2, Qt.AlignTop)
        grid.addWidget(self.proxy_password, 5, 3, Qt.AlignTop)
        grid.setRowStretch(7, 1)

        # Blockchain tab
        blockchain_layout = QVBoxLayout(blockchain_tab)
        form = FormSectionWidget()
        self.status_label = QLabel('')
        form.add_row(_('Status'), self.status_label, True)
        self.server_label = QLabel('')
        form.add_row(_('Server'), self.server_label, True)
        self.height_label = QLabel('')
        form.add_row(_('Blockchain'), self.height_label, True)
        blockchain_layout.addWidget(form)

        self.split_label = QLabel('')
        form.add_row(QLabel(""), self.split_label)

        self.nodes_list_widget = NodesListWidget(self)
        blockchain_layout.addWidget(self.nodes_list_widget)
        blockchain_layout.addStretch(1)

        vbox = QVBoxLayout()
        vbox.addWidget(tabs)
        vbox.setSizeConstraint(QVBoxLayout.SetFixedSize)
        self.layout_ = vbox

        # Tor detector
        self.td = td = TorDetector()
        td.found_proxy.connect(self.suggest_proxy)
        td.start()

        self.last_values = None
        self.fill_in_proxy_settings()
        self.update()

    def check_disable_proxy(self, b):
        if not self.config.is_modifiable('proxy'):
            b = False
        for w in [self.proxy_mode, self.proxy_host, self.proxy_port,
                  self.proxy_username, self.proxy_password]:
            w.setEnabled(b)

    def enable_set_server(self):
        if self.config.is_modifiable('server'):
            enabled = not self.autoconnect_cb.isChecked()
            self.server_host.setEnabled(enabled)
            self.server_port.setEnabled(enabled)
            self.servers_list.setEnabled(enabled)
        else:
            for w in [self.autoconnect_cb, self.server_host, self.server_port, self.servers_list]:
                w.setEnabled(False)

    def update(self):
        server = self.network.main_server
        self.server_host.setText(server.host)
        self.server_port.setText(str(server.port))
        self.autoconnect_cb.setChecked(self.network.auto_connect())

        host = server.host if self.network.is_connected() else _('None')
        self.server_label.setText(host)

        self.set_protocol(server.protocol)
        self.servers = self.network.get_servers()
        self.servers_list.update(self.servers, self.protocol, self.tor_cb.isChecked())
        self.enable_set_server()

        height_str = "%d "%(self.network.get_local_height()) + _('blocks')
        self.height_label.setText(height_str)
        n = len(self.network.sessions)
        status = _("Connected to {:d} servers.").format(n) if n else _("Not connected")
        self.status_label.setText(status)

        chains = self.network.sessions_by_chain().keys()
        if len(chains) > 1:
            our_chain = self.network.chain()
            heights = set()
            for chain in chains:
                if chain != our_chain:
                    _chain, common_height = our_chain.common_chain_and_height(chain)
                    heights.add(common_height + 1)
            msg = _('Chain split detected at height(s) {}\n').format(
                ','.join(f'{height:,d}' for height in sorted(heights)))
        else:
            msg = ''
        self.split_label.setText(msg)
        self.nodes_list_widget.update(self.network)



    def fill_in_proxy_settings(self):
        """Populate the proxy tab UI with current network proxy settings.

        - Auto-configures Tor proxy on TAILS silently.
        - Shows a warning on non-TAILS if the user enables a proxy manually.
        """
        self.filling_in = True

        # Determine if we are on TAILS
        on_tails = is_tails_os()

        # Only auto-set proxy on TAILS
        if on_tails and not self.network.proxy:
            proxy_to_use = SVProxy('127.0.0.1:9050', 'SOCKS5', None)
            self.network.set_proxy(proxy_to_use)
        else:
            proxy_to_use = self.network.proxy or None

        # Populate UI fields
        self.proxy_cb.setChecked(proxy_to_use is not None)
        if proxy_to_use:
            self.proxy_mode.setCurrentText(proxy_to_use.kind())
            self.proxy_host.setText(str(proxy_to_use.host()))
            self.proxy_port.setText(str(proxy_to_use.port()))
            self.proxy_username.setText(proxy_to_use.username())
            self.proxy_password.setText(proxy_to_use.password())
        else:
            self.proxy_mode.setCurrentText("SOCKS5")
            self.proxy_host.setText("")
            self.proxy_port.setText("")
            self.proxy_username.setText("")
            self.proxy_password.setText("")

        self.check_disable_proxy(proxy_to_use is not None)
        self.filling_in = False

        # Kickstart network on TAILS silently
        if on_tails and proxy_to_use:
            try:
                if self.network.auto_connect():
                    server = self.network.main_server or (
                        self.network.get_servers()[0] if self.network.get_servers() else None
                    )
                    if server:
                        self.network.set_server(server, True)
            except Exception as e:
                logger.warning(f"Failed to auto-connect network on TAILS: {e}")

            self.update()

        # Non-TAILS warning if user manually enables proxy
        def warn_if_not_tails(checked):
            if checked and not on_tails:
                MessageBox.show_warning(
                    _("You are enabling a proxy, but Tor is not running. "
                      "Make sure you have Tor installed and running if you want privacy.")
                )

        self.proxy_cb.clicked.disconnect()
        self.proxy_cb.clicked.connect(self.check_disable_proxy)
        self.proxy_cb.clicked.connect(self.set_proxy)
        self.proxy_cb.clicked.connect(warn_if_not_tails)






    def layout(self):
        return self.layout_

    def set_protocol(self, protocol):
        if protocol != self.protocol:
            self.protocol = protocol

    def follow_server(self, server):
        self.network.set_server(server, self.network.auto_connect())
        self.update()

    def _on_autoconnect_toggled(self, _checked):
        self.set_server()

    def set_server(self, server=None):
        values = (self.server_host.text(), self.server_port.text(),
                  self.network.main_server.protocol, self.autoconnect_cb.isChecked())
        if values != self.last_values:
            self.last_values = values
            try:
                if not server:
                    server = SVServer.unique(*values[:3])
                self.network.set_server(server, self.autoconnect_cb.isChecked())
            except Exception as e:
                MessageBox.show_error(str(e))

    def set_proxy(self):
        if self.filling_in:
            return
        proxy = None
        if self.proxy_cb.isChecked():
            try:
                address = NetAddress(self.proxy_host.text(), self.proxy_port.text())
                test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                test_sock.settimeout(3.0)
                try:
                    test_sock.connect((address.host, address.port))
                except Exception:
                    MessageBox.show_warning(
                        _("Tor not reachable yet at {}:{} — retrying automatically. "
                          "Leave proxy enabled and wait 30–60 seconds.").format(
                            address.host, address.port
                        )
                    )
                finally:
                    test_sock.close()
                auth = SVUserAuth(self.proxy_username.text(), self.proxy_password.text()) if self.proxy_username.text() else None
                proxy = SVProxy(address, self.proxy_mode.currentText(), auth)
            except Exception as e:
                logger.exception('error setting proxy')
                MessageBox.show_error(str(e))
                return

        if not proxy:
            self.tor_cb.setChecked(False)

        self.network.set_proxy(proxy)

    def suggest_proxy(self, found_proxy):
        self.tor_proxy = found_proxy
        self.tor_cb.setText("Use Tor proxy at port " + str(found_proxy[1]))
        if (self.proxy_cb.isChecked() and
                self.proxy_mode.currentText() == 'SOCKS5' and
                self.proxy_host.text() == found_proxy[0] and
                self.proxy_port.text() == str(found_proxy[1])):
            self.tor_cb.setChecked(True)
        self.tor_cb.show()

    def use_tor_proxy(self, use_it: bool):
        if use_it:
            # Sync both boxes
            self.proxy_mode.setCurrentText('SOCKS5')
            self.proxy_host.setText(self.tor_proxy[0])
            self.proxy_port.setText(str(self.tor_proxy[1]))
            self.proxy_username.setText("")
            self.proxy_password.setText("")
            self.proxy_cb.setChecked(True)
        else:
            # Disable both if Tor is turned off
            self.proxy_cb.setChecked(False)

        self.check_disable_proxy(self.proxy_cb.isChecked())
        self.set_proxy()


    def proxy_settings_changed(self):
        # Keep Tor box synced
        proxy_host = self.proxy_host.text().strip()
        proxy_port = self.proxy_port.text().strip()
        mode = self.proxy_mode.currentText()

        if (mode == 'SOCKS5' and
            proxy_host in ('127.0.0.1', 'localhost') and
            proxy_port in ('9050', '9150')):
            self.tor_cb.blockSignals(True)
            self.tor_cb.setChecked(True)
            self.tor_cb.blockSignals(False)
        else:
            self.tor_cb.blockSignals(True)
            self.tor_cb.setChecked(False)
            self.tor_cb.blockSignals(False)


class TorDetector(QThread):
    found_proxy = pyqtSignal(object)

    def run(self):
        ports = [9050, 9150]
        for p in ports:
            pair = ('localhost', p)
            if TorDetector.is_tor_port(pair):
                self.found_proxy.emit(pair)
                return

    @staticmethod
    def is_tor_port(pair):
        try:
            s = (socket._socketobject if hasattr(socket, "_socketobject")
                 else socket.socket)(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.1)
            s.connect(pair)
            s.send(b"GET\n")
            if b"Tor is not an HTTP Proxy" in s.recv(1024):
                return True
        except socket.error:
            pass
        return False

