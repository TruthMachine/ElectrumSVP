"""Small AppKit bridge for Qt 5, without a PyObjC dependency.

NSApplication.appearance affects this process only. None restores system tracking.
All callers run on the GUI thread; no system preferences are written.
"""
import ctypes
from functools import lru_cache


@lru_cache(maxsize=1)
def _runtime():
    appkit = ctypes.CDLL('/System/Library/Frameworks/AppKit.framework/AppKit')
    objc = ctypes.CDLL('/usr/lib/libobjc.A.dylib')
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    return appkit, objc


def _class(name):
    return _runtime()[1].objc_getClass(name)


def _send(receiver, selector, *args, result=ctypes.c_void_p, argument=ctypes.c_void_p):
    objc = _runtime()[1]
    signature = ctypes.CFUNCTYPE(result, ctypes.c_void_p, ctypes.c_void_p,
        *([argument] * len(args)))
    return signature(('objc_msgSend', objc))(
        receiver, objc.sel_registerName(selector), *args)


def _application():
    return _send(_class(b'NSApplication'), b'sharedApplication')


def set_appearance(dark):
    appearance = None
    if dark is not None:
        name = _send(_class(b'NSString'), b'stringWithUTF8String:',
            b'NSAppearanceNameDarkAqua' if dark else b'NSAppearanceNameAqua',
            argument=ctypes.c_char_p)
        appearance = _send(_class(b'NSAppearance'), b'appearanceNamed:', name)
    _send(_application(), b'setAppearance:', appearance, result=None)


def is_dark():
    appearance = _send(_application(), b'effectiveAppearance')
    name = _send(appearance, b'name')
    value = _send(name, b'UTF8String', result=ctypes.c_char_p) or b''
    return b'Dark' in value
