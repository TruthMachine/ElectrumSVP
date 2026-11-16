# ElectrumSVP hardware wallet integration

This branch restores optional hardware accounts with a shared adapter contract. It is an
experimental integration, not a blanket certification of every model, firmware or BSV script.
The device signs; ElectrumSVP constructs and validates transactions and, in normal wallet use,
broadcasts through its existing BSV network connection. The integration tests described below
have **never broadcast or spent the owner's funds**.

## Design and the earlier removal

SVP 0.1.0 intentionally removed the hardware entry points to reduce complexity and fragile
external dependencies ([release](https://github.com/TruthMachine/ElectrumSVP/releases/tag/v0.1.0)).
The device manager, keystores and brand implementations remained. Merely restoring the menu
would retain the old coupling and several broken cancellation/threading paths.

The new `electrumsv.devices.hardware` module provides discovery, connection, public keys,
address display, message signing and transaction signing. A lazy registry owns vendor imports,
optional dependency groups and capability checks. `DeviceMgr` still owns the single transport
and pairing cache; there is no second cache or wallet format. Qt constructs handlers on the GUI
thread and runs USB operations on workers. Account import remains public-key-only. Initialize
or recover a device through its manufacturer application, not this wizard.
Existing hardware accounts can still load public keys and metadata if their optional SDK is
missing; signing reports the missing dependency and requires reopening after installation.

Signing operates on a transaction copy. Every new signature must verify against the original
BSV transaction and the expected public key with `0x41` before any signatures are applied.
The public external-signer interface and partial/multisig flow are documented in
[external-signers.md](hardware/external-signers.md). It supports capability-declared sighashes;
the Ledger adapter remains limited to `0x41`. Message signatures are likewise verified against the exact message and derived public key.
Cancellation or invalid responses leave the original transaction unchanged. Software wallets
continue to use their existing signing path.

Device operations share a lock so a scan or timeout cannot interfere with a signing request.
Ledger transport reads have bounded deadlines, including continuation packets. An OS access
failure is reported as unavailable/possibly occupied; it does not prove which other application
owns the device. Close Ledger Wallet/Live, browser device sessions and other wallet applications,
then retry. No process is killed and no device is forcibly detached.

USB identity is not wallet identity. A model-family discriminator prevents identical serial
fields on a Nano S and S Plus from colliding. Same-model duplicate serials are disambiguated by
USB path. An app switch reconnects only to the selected identity; ambiguous/changing identities
require selection again. Pairing must still compare the complete account xpub. Never silently
select the first attached Ledger.

## Firmware catalogue and tested versions

The dated, machine-readable catalogue and our own results are in
[validation.json](hardware/validation.json). Manufacturer releases, source code versions,
owner-reported OS versions, device-read app versions and our physical tests are separate evidence.
“Latest” is a lookup as of **2026-11-16**, not a runtime promise or an instruction to downgrade.

| Brand / model | Latest firmware located | BSV route / scope |
| --- | --- | --- |
| Ledger Nano S Plus | OS 1.7.0, 2026-10-01 | BCH app 2.4.14 read from physical device; public legacy source changelog currently ends at 2.4.13 |
| Ledger Nano X | OS 2.8.0, 2026-10-01 | BCH app 2.4.14 read from physical device; installed Bitcoin 2.5.1 is not the signing route |
| Ledger Nano S | OS 2.1.0 read from connected device's BOLOS response | Historical adapter; current legacy source removed Nano S builds in 2.4.9. Do not apply S Plus firmware/app assumptions to it |
| Trezor Model One | Universal 1.14.2, 2026-10-21 | Retained Bcash protocol adapter; no physical validation by us |
| Trezor Model T | Universal 2.13.1, 2026-11-11 | Retained Bcash protocol adapter; no physical validation by us |
| KeepKey | Multi-coin stable 7.14.2, 2026-10-16 | Retained BitcoinSV adapter; no physical validation by us |
| KeepKey other releases | 7.14.3 Bitcoin-only stable; 7.16.0 multi-coin prerelease | Neither is substituted for the multi-coin stable baseline |
| Digital Bitbox / BitBox01 | Last official repository release 7.1.0, 2020-08-05 | Legacy adapter, no on-device screen; no physical validation by us |

Trezor Safe 3/5/7 firmware is also currently 2.13.1, but these models are outside the restored
Model One/T adapter's claimed scope. BitBox02 is a different protocol and is not BitBox01 support.
Likewise Stax, Flex and Nano Gen 5 are not certified by adding their USB identifiers. Original
Nano S, Blue and HW.1 combinations need separate verification; modern Nano testing does not
certify historical devices. Ledger and KeepKey transaction signing in this adapter is limited
to BSV mainnet. Testnet xpub encoding tests are not testnet device signing evidence.

Sources:

- [Nano S Plus OS releases](https://support.ledger.com/ja/article/4494540771997-zd)
- [Nano X OS releases](https://support.ledger.com/article/360014980580-zd)
- [Ledger legacy source changelog](https://github.com/LedgerHQ/app-bitcoin-legacy/blob/fa287121d830a9ea4518babd09993764d32439fb/CHANGELOG.md)
- [Trezor firmware versions](https://trezor.io/other/product-updates/trezor-firmware-changelog-all-versions-and-release-dates)
- [KeepKey releases](https://github.com/keepkey/keepkey-firmware/releases)
- [BitBox01 firmware](https://github.com/BitBoxSwiss/mcu/releases/tag/v7.1.0)
- [BitBox01 lifecycle notice](https://guides.shiftcrypto.ch/bitbox01/)

## Current physical evidence

The owner operated the physical buttons on macOS / Apple Silicon, with Python 3.10.20 and
`electrumsv-btchip-python 0.1.32`, `hidapi 0.10.0`. Public keys, addresses and signatures are not
published in this repository. OS versions for S Plus/X were read by the owner in device settings;
app versions were read over USB. These are direct protocol tests through the adapters, not a
claim that a packaged application or every GUI flow has been physically tested.

| Combination | Public key | Address screen + public-key comparison | Reject signing | Offline v2 / 0x41 transaction signature | Message signature |
| --- | --- | --- | --- | --- | --- |
| Nano S Plus / OS 1.7.0 / BCH 2.4.14 | Passed, coin 0 and 145 account paths | Passed, owner confirmed | Passed | Passed, cryptographically verified | Passed, cryptographically verified |
| Nano X / OS 2.8.0 / BCH 2.4.14 | Passed, coin 0 account path | Passed, owner confirmed | Passed | Passed, cryptographically verified | Passed, cryptographically verified |
| Nano S / OS 2.1.0 / BCH 2.4.7 | Passed, coin 0 account path | Passed, owner confirmed | Passed | Passed, cryptographically verified | Passed, cryptographically verified |
| Other brands / combinations | No physical record | No physical record | No physical record | No physical record | No physical record |

The initial offline transaction references a deliberately synthetic outpoint and has one P2PKH input,
one output back to the test key, 900 output satoshis, 100 nominal fee satoshis and version 2.
It proves the APDU/signature path, not that a real UTXO is unspent, mature or broadcastable.
No real UTXOs were loaded, no wallet files were opened and no broadcast was attempted.
The rejection test checks that no signature was left behind.

Nano S additionally passed trusted-parent 2-input/2-output P2PKH, partial P2PKH signing, and
bare 2-of-2 multisig with synthetic coinbase parents. The owner confirmed no unverified-input
warning in all three. Partial/multisig fixtures roundtrip signing JSON, preserve existing
public-test-key signatures and verify new Ledger signatures. S Plus/X subsequently passed the same partial and bare-multisig fixtures, with
existing signatures preserved and no unverified-input warning confirmed by the owner.
All three models also completed trusted 256- and 512-input P2PKH fixtures. See
[measurements](hardware/ledger-benchmarks.json) and [Known Issues](hardware/known-issues.md).

Additional physical checks: simultaneous S/S Plus identities, explicit locked responses,
S Plus app opening and reconnect, and an exclusive-access conflict from a second probe process.
That conflict is not a test against Ledger Wallet itself. S's initial app opening reached BCH
but its immediate identity query timed out; the entire opening flow has not been repeated.
Later controlled S queries accepted **both four and five bytes** three times each. The earlier
claim that the fifth byte caused the timeout was retracted; see [history](hardware/ledger-history.md).

A validated production combination additionally needs real test-coin transaction scenarios,
multiple inputs, change, reconnect/reopen, packaged OS builds and any claimed multisig/script
cases. Those funded tests are **outside the owner's authorization for this session**.

## Ledger details and warnings

Use Bitcoin Cash, not the modern Bitcoin app. BCH's device-side `SIGHASH_ALL | FORKID` is `0x41`.
The new Bitcoin app and `ledger-bitcoin` PSBT client are not drop-in substitutes for this route.
The old setup probe derived a forbidden parent under BCH 2.4.10 path hardening; the new setup
probe is `m/44'/145'/0'`. It does not change the account's actual path or existing addresses.
Keep the original account path when reopening a wallet, commonly `m/44'/0'/0'` or
`m/44'/145'/0'`. Coin type `236'` is not enabled by simply choosing it on the host.

The client checks the application identity. When the selected device is unlocked, it can
request opening Bitcoin Cash, then reconnect and recheck the identity. This can require a
button confirmation. It never enters a PIN or installs an app. Some locked states expose no
HID interface; others explicitly return `0x5515`. Missing, busy and locked are not interchangeable.
[Ledger's app commands](https://developers.ledger.com/docs/device-interaction/dmk-ts/beginner/exchange_data)
describe opening apps and the required user interactions.

The adapter streams available full previous transactions to Ledger's trusted
input protocol. It first checks parent hash, output index, amount and script. The device can then
bind input amounts to those parents, avoiding the **unverified inputs** warning in the tested
Nano S cases. This does not establish that parents are on-chain, unspent or mature.

Ledger uses parents already supplied in signing JSON or available in the local wallet cache.
It does not force network downloads of missing optional parents; the explicit reduced-verification
prompt remains reachable offline. Adapters that require full parents retain their strict retrieval.

Streaming is bounded by provisional host work budgets:

| Model group | Inputs / outputs | Per-parent / total bytes | Max individual script | Parent APDU budget |
| --- | --- | --- | --- | --- |
| Historical / unknown models | 4 / 8 | 32 / 64 KiB | 8 KiB | 512 |
| Nano S | 512 / 32 | 64 / 512 KiB | 16 KiB | 8192 |
| Nano S Plus / Nano X | 1024 / 128 | 512 / 2048 KiB | 128 KiB | 32768 |

These are host work policies, **not measured firmware limits**. All three models passed
512-input fixtures with small synthetic parents; 1024 is an unmeasured policy allowance.
More than 4 inputs on Nano S (16 on S Plus/X) asks for a waiting-time confirmation.
This does not reduce verification. Parent bytes, script lengths and command count also
matter, so input count alone cannot predict completion time.

Every Ledger signing operation offers **Verify previous transactions on the device**,
defaulting to on. Switching it off omits complete parents, not the current inputs/outputs,
and requires an explicit reduced-verification confirmation. It is not persisted as a
wallet-wide preference. Missing parents or exceeding a budget also prompts for that
choice; cancelling leaves signatures unchanged. A supplied in-budget parent with a wrong
hash, amount or script is rejected. A device error never silently switches to unverified
signing. The warning and weaker fee assurance remain on the explicitly chosen reduced route.

The Qt status window shows parent verification, current-transaction preparation, device
review and per-input signing progress, plus elapsed time. A long request reuses one window.
It does not claim a remaining-time estimate or prevent auto-lock. The 512-input S Plus
retry took 487.5 seconds including review; 113.8 seconds were parent verification, 65.2
current-input preparation and 278.9 signing commands. Earlier measurements exclude
preparation and human review. [Known Issues](hardware/known-issues.md) records the preceding
PIN-lock interruptions as well as the successful retry. No 400-input cutoff was added.

“Nonstandard BSV transaction” is not a single capability. Message signing, P2PKH transactions,
P2SH multisig, data outputs, large scripts and arbitrary contracts need distinct tests and device
review semantics. This patch does not add raw-digest signing as a workaround for firmware policy.
A signature over a host-supplied hash is not evidence that the screen reviewed the transaction.

A separate BSV device app is a possible development project, not a prerequisite or a deliverable
of this host patch. Nano S Plus supports development sideloading; Nano X does not support ordinary
sideloading. Deployment on Nano X needs Ledger's distribution path. No custom apps, developer
certificates, firmware changes or device resets were performed here.
[Official restriction](https://developers.ledger.com/docs/device-app/beginner/vscode-extension).

The Ledger message dialog shows SHA-256 of the exact UTF-8 text, including whitespace,
matching the hash-only device display. Host text is not presented as device-verified plaintext.
Message signing uses the existing Bitcoin Signed Message convention, not transaction signing
or BIP322. An application using it for authentication should specify its own readable domain,
nonce, expiration and purpose; a generic signature is not a safe reusable authorization token.

## Previous transactions and mining rewards

Ledger, Trezor and KeepKey receive the transaction's actual version. The previous default
of 1 could produce invalid signatures for version-2 transactions. Trezor's previous-transaction
encoding preserves a coinbase's null outpoint, `0xffffffff` index and script. KeepKey accepts
previous transactions supplied by another cosigner. A dedicated hardware regression covers
this vendor encoding; synthetic coinbase parents are also used in the Ledger fixtures.

Selected-chain membership, reward maturity and orphan/reorg handling belong to the separate
Coinbase lifecycle PR. This hardware PR does not change balance classification, coin selection,
proof invalidation or databases. Trusted-input verification authenticates the supplied parent
transaction, not its confirmation or spendability on the current chain.

## Installation and test procedure

The base installation does not require the vendor SDKs. Optional groups are `hardware-ledger`,
`hardware-trezor`, `hardware-keepkey`, `hardware-bitbox01`; `hardware` retains the aggregate set.
For example, `python -m pip install '.[hardware-ledger]'`. Frozen builds collect adapter modules
explicitly because imports are now lazy. The local Universal macOS build recipe is submitted
separately from this hardware integration.

The existing SDK pins are compatibility baselines, not claims of the newest SDK. In particular,
Trezor 0.12's interface and KeepKey/protobuf need separate modernization work. Historical macOS
exit-crash and Windows protobuf-corruption reasons are preserved in requirements-hw.txt.
The per-brand groups prevent installing every legacy SDK for a Ledger-only source checkout.

Run software regressions (headless Qt: set `QT_QPA_PLATFORM=offscreen`):

```sh
python -m pytest -q electrumsv/tests/test_hardware.py electrumsv/tests/test_hardware_qt.py \
  electrumsv/tests/test_ledger.py electrumsv/tests/test_ledger_transport.py \
  electrumsv/tests/test_ledger_session.py electrumsv/tests/test_ledger_input_verification.py \
  electrumsv/tests/test_external_signing.py electrumsv/tests/test_hardware_coinbase.py \
  electrumsv/tests/test_keystore.py electrumsv/tests/test_transaction.py \
  electrumsv/tests/test_ledger_progress.py electrumsv/tests/test_ledger_capabilities.py
```

For physical tests run `python -m contrib.hardware_wallet_probe --mode status`, then select an
exact `--device-id` for `read`, `display`, `reject`, `sign`, `partial`, `multisig` or `message`.
Signing fixtures default to trusted synthetic parents; `--input-mode unverified` explicitly
selects the old simple fixture. Never guess which device
is first. The probe is offline and has no broadcast operation. For the old macOS hidapi build,
headless imports may need AppKit loaded first; the probe does this automatically. Building that
pin locally may require Cython < 3 and setuptools < 66 with build isolation disabled.

Dated device evidence is recorded in `hardware/validation.json`. The isolated hardware
branch passes 273 focused regressions, including its external-signing prerequisite.
Appearance, Coinbase and catalogue tests are run in their own branches.
The inherited full suite has a removed bitcoinx cashaddr API collection failure and ten wallet
failures reproduced on the baseline (legacy seed metadata, testnet fixture migration and removed
multisig helpers). No clean full-suite result is claimed.

## UI acceptance and remaining physical checks

- **Add account → Connect hardware wallet**: lazy discovery isolates missing vendors, reports
  access errors, and requires an explicit selection when several devices are found. The account
  path is configurable and becomes part of its label. No seed recovery UI is exposed.
- **Tools → Hardware wallets** and the **Hardware wallet** toolbar action open an independent,
  dismissible window listing only detected physical devices, across brands. A highlighted row
  is a candidate: **Connect** checks the device and the current account's full xpub at its
  stored path; only a passing row gets a checkmark and enables **Select**. Cancel or closing
  the window discards the choice, including late connection results. Selecting a device is
  a session preference; signing rechecks its full xpub and never silently falls back to a
  different attached device.
- The toolbar distinguishes no detected devices (red cross), detected but unavailable for
  the current account (warning), and a matching account xpub (green check). Background reads
  refresh every five seconds and invalidate matches on account changes. Automatic probes only read identity, lock and application state; they never connect,
  read account keys or select a device. Rows update in place, preserving focus, selection
  and scrolling. **Connect** can request BCH opening on the chosen unlocked Ledger and
  checks the account xpub; **Select** remains a separate user action. Failed checks retain
  precise physical states such as Locked. Background polling cannot trigger PIN/passphrase prompts. No host code enters a PIN. Ledger FIDO/U2F interfaces are
  excluded from wallet candidates, preventing duplicate unavailable rows and the former
  `NoneType.exchange` error.
- **Keys → Show address on device / Sign/verify message**: address responses are compared with
  the derived wallet key; message text/hash are preserved exactly. Unsupported hardware
  encrypt/decrypt actions are omitted.
- **Transaction → Sign selected inputs…**: Ledger-only account entry, complete transaction
  review, existing signatures preserved, incomplete JSON can be saved and passed on. Signing
  does not broadcast.
- Ledger signing options, preparation, previous-transaction checks, device review, per-input
  signatures and local verification share one fixed-size window. Counts advance only after
  device responses. Cancel stops before the next device operation; an already pending device
  request must first finish or be rejected. Legacy 2FA authentication retains its dedicated UI.
  This new presentation is simulated-UI tested; earlier physical evidence does not certify it.

Headless Qt tests cover worker ownership, cancellation, stale discovery completion, device
selection, account paths, hash display and partial-input selection. The dismissible picker was
also reviewed with simulated devices. These are not physical GUI signing tests. Still required:
P2SH multisig, same-model devices, actual Ledger Wallet contention, large real-parent latency,
and hardware signing through the packaged GUI. Nano S's initial app-switch timeout has not been
reproduced or explained; later signing successes started with BCH already open.

## Languages

All new user-facing strings use gettext. The 37-language catalogue update, locale selector
fixes and compiler are submitted in a separate PR; untranslated strings fall back to English.
That PR tests coverage and formatting for all 183 new interface strings, including 173 hardware
messages. Non-Chinese additions still require native-language review.
