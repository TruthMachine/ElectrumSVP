from typing import Callable, NamedTuple, Optional
from mypy_extensions import Arg, DefaultArg

import sys
sys.modules['electrumsv.types'] = sys.modules[__name__]

class TxoKeyType(NamedTuple):
    tx_hash: bytes
    tx_index: int

WaitingUpdateCallback = Callable[[Arg(bool, "advance"), DefaultArg(Optional[str], "message")], None]
