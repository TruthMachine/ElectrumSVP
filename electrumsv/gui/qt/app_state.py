# ElectrumSV - lightweight Bitcoin SV client
# Copyright (C) 2019-2020 The ElectrumSV Developers
# Copyright (C) 2012 thomasv@gitorious
#
# MIT License

'''QT application state.'''

import sys
from electrumsv.app_state import AppStateProxy, AppState
from .app import SVApplication


class QtAppStateProxy(AppStateProxy):

    def __init__(self, config, gui_kind='qt'):
        # Initialize base proxy
        super().__init__(config, gui_kind)

        # Set the global proxy so app_state.config exists
        AppState.set_proxy(self)

        # Create the Qt application instance
        self.app = SVApplication(sys.argv)
        self.set_app(self.app)

    def has_app(self):
        return True

    def set_base_unit(self, base_unit: str):
        """Update base unit and notify the GUI if changed."""
        if super().set_base_unit(base_unit):
            self.app.base_unit_changed.emit()
