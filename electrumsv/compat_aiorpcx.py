# compat_aiorpcx.py
import aiorpcx
import traceback
import logging

log = logging.getLogger("compat_aiorpcx")

# wrap SessionBase._process_messages to log on exit / exception
_orig_proc = aiorpcx.session.SessionBase._process_messages
async def _wrapped__process_messages(self, recv_message):
    try:
        return await _orig_proc(self, recv_message)
    except Exception:
        log.exception("SessionBase._process_messages exception; stack at exception:\n%s",
                      "".join(traceback.format_stack()))
        raise
    finally:
        log.debug("SessionBase._process_messages exiting; stack:\n%s",
                  "".join(traceback.format_stack()))

aiorpcx.session.SessionBase._process_messages = _wrapped__process_messages

# wrap RSTransport.process_messages to catch errors and log
if hasattr(aiorpcx.rawsocket, 'RSTransport'):
    _orig_rst = aiorpcx.rawsocket.RSTransport.process_messages
    async def _wrapped_rst_process_messages(self):
        try:
            return await _orig_rst(self)
        except Exception:
            log.exception("RSTransport.process_messages exception; stack:\n%s",
                          "".join(traceback.format_stack()))
            raise
        finally:
            log.debug("RSTransport.process_messages finished; stack:\n%s",
                      "".join(traceback.format_stack()))
    aiorpcx.rawsocket.RSTransport.process_messages = _wrapped_rst_process_messages

# simply re-export real aiorpcx
from aiorpcx import *

