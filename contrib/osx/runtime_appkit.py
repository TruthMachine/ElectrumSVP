"""Make the native symbols used by the pinned hidapi available before vendor imports."""
import ctypes

ctypes.CDLL('/System/Library/Frameworks/AppKit.framework/AppKit', mode=ctypes.RTLD_GLOBAL)
