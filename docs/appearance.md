# Appearance

This patch preserves the wallet's page order and main layout. It repairs theme switching,
control spacing and contrast, native typography, segmented tabs, and palette-aware icons.
Light and Dark explicitly override the platform appearance; System follows it. Orange and
Pink also provide their own accessible selection/focus colors.

UI glyphs use a vendored subset of Lucide (ISC license included under
`electrumsv/data/icons/lucide/LICENSE`). Vendor logos retain their identities. No font or
icon download is required at runtime. See Apple Human Interface Guidelines on
[dark mode](https://developer.apple.com/design/human-interface-guidelines/dark-mode),
[typography](https://developer.apple.com/design/human-interface-guidelines/typography) and
[segmented controls](https://developer.apple.com/design/human-interface-guidelines/segmented-controls).
The Qt implementation follows those principles; it does not claim to be a native AppKit UI.

## Validation

- Theme and layout regressions are in `test_theme.py` and `test_page_layout.py`.
- macOS native rendering: main pages, Preferences, Network and simulated hardware windows;
  Light/Dark, Orange/Pink, English and Chinese. These captures were made on the combined
  development branch; the isolated changes also have Qt regression coverage.
- New translations are submitted separately; untranslated messages fall back to English.
- Linux cloud audit of integration commit `2d88c95c9489c9e2c65e60d56b553f55a173c193`:
  Debian 13.6 x86_64, Xorg + xfwm4, Python 3.10.22, PyQt5 5.15.11, Qt 5.15.19.
  294 captures across 10 scenarios, 7 main pages, 7 preference tabs, Network and simulated
  device states. 66 Qt tests passed. Light/Dark, English/Chinese, 100/200% scaling,
  760x570 and 1100x720 windows were covered.
- That Linux audit found large-font Send field compression and Receive field/QR overlap.
  This branch adds form scrolling to preserve minimum control sizes, with automated geometry
  checks and native macOS large-font captures. The final scrolling fix has not been rerun
  on Linux. Hardware status column elision is addressed in the independent hardware patch.

## Remaining limits

System resolved to Light in the Linux audit; dynamic desktop theme changes and Wayland were
not exercised. Some inherited UI text remains untranslated. The full upstream
test suite also has unrelated legacy wallet/cash-address failures; the focused run above is
not a claim that the entire suite passes.
