# Appearance and transaction inspection

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

## Transaction inspection

An account name appears on an input only when its outpoint belongs to that account.
Missing amounts no longer sum to a misleading zero. The dialog reads missing previous
transactions from the existing connection, checks the transaction hash and output index,
and computes the fee once all input amounts are known. Requests are deduplicated with at
most four concurrent reads. Results stay in the dialog; they do not change wallet ownership,
UTXOs, signing data or database contents. Closing the dialog stops queued lookups. Offline,
timeout and invalid-response cases retain unknown values and offer Retry. The previous
transaction can be inspected from the input once fetched. Nonstandard script display is
also updated for the project's bitcoinX 0.9+ API.

## Validation

- Independent branch: 100 focused Qt, theme, input-lookup and translation tests passed.
- macOS native rendering: main pages, Preferences, Network and simulated hardware windows;
  Light/Dark, Orange/Pink, English and Chinese. Input lookup and fee display use synthetic
  transactions and a simulated server, never a funded wallet or device.
- Seven appearance/inspection messages are present in all 37 non-English catalogues.
  Non-Chinese additions are machine-generated drafts needing native review.
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
not exercised. Some inherited UI text remains untranslated. The latest input-lookup UI was
validated with simulated responses, not the owner's historical transactions. The full upstream
test suite also has unrelated legacy wallet/cash-address failures; the focused run above is
not a claim that the entire suite passes.
