# Ledger Bitcoin Cash adapter

Use the **Bitcoin Cash** device app for BSV. The modern Bitcoin app's PSBT interface
is not the BCH `0x41` signing protocol. The host assembles and validates transactions;
network broadcasting remains a separate wallet action.

See [hardware integration and current limits](../../../docs/hardware-wallets.md),
[firmware / physical validation records](../../../docs/hardware/validation.json), and
[protocol compatibility history](../../../docs/hardware/ledger-history.md).

This adapter now provides:

- Nano S Plus and Nano X enumeration alongside retained Nano S IDs.
- Distinct labels for devices with colliding serial fields, and exact-device app switching.
- App-information queries using Ledger's five-byte encoding. Repeated Nano S / BCH 2.4.7
  tests accepted both four and five bytes; the earlier byte-count diagnosis was retracted.
- BCH app identity checking, optional automatic app opening after PIN unlock, and bounded
  reconnects to the selected identity. It never enters PINs, installs apps or changes firmware.
- A setup probe at `m/44'/145'/0'` compatible with the path restriction introduced in BCH
  2.4.10. Existing account derivation paths and xpub fingerprints remain unchanged.
- Transaction-version propagation, signature verification, message-signature validation,
  bounded USB reads and actionable access-conflict errors.

Small transactions now use bounded trusted-parent streaming. Nano S / OS 2.1.0 / BCH 2.4.7
passed synthetic 2-in/2-out P2PKH, partial signing and bare 2-of-2 multisig with no unverified-input
warning. Existing signatures survived JSON roundtrips unchanged. S Plus/X still need these
new physical cases. Budgets are host policy, not measured firmware maxima.

If parents are missing or exceed a budget, the owner can explicitly choose reduced verification;
then **unverified inputs** may appear. Invalid in-budget parents are rejected, and device errors
never silently downgrade. Trusted inputs do not prove chain membership, unspentness or maturity.
Only `0x41` is requested from BCH. See the [external signing contract](../../../docs/hardware/external-signers.md)
for partial-signature admission, script scope and the PSBT boundary.

Our physical tests use synthetic, unspendable transaction inputs. No real wallet files or
UTXOs were opened and no transaction was broadcast. Public keys, addresses, USB serials and
signatures from the owner's devices are not stored in the published validation records.

Run `python -m contrib.hardware_wallet_probe --mode status` to list devices, then select
one with `--device-id` for `read`, `display`, `reject`, `sign`, `partial`, `multisig` or `message`. The probe has no
broadcast mode. App switching may require a button confirmation.

On Apple Silicon, building the old hidapi pin may require Cython < 3 and setuptools < 66
with build isolation disabled. Headless imports need AppKit loaded first (the probe does
this automatically). The pins retain the original project's macOS/Windows compatibility
rationale; the per-vendor extras keep them optional.
