# Ledger signing scope

Ledger BCH only requests `0x41`. Historical app 2.4.7 and public 2.4.13 source explicitly reject
other types; installed 2.4.14 is treated as retaining that restriction, without claiming a
negative physical test or an exact public-source mapping for that binary.

Under `0x41`, Nano S / OS 2.1.0 / BCH 2.4.7 physically passed synthetic 2-input/2-output:

- P2PKH partial signing: input 0 already signed by a public test key; Ledger signs input 1.
- Bare 2-of-2 multisig: a public test key already signed both inputs; Ledger adds its own two
  signatures. Existing signatures survive a signing-JSON roundtrip unchanged.

Both used trusted previous-input handling, cryptographic signature verification and owner
confirmation of no unverified-input warning. These are unspendable offline fixtures, not funded
transactions. P2SH multisig and selecting a subset of otherwise eligible Ledger inputs have
host-side regressions; the P2SH script-interpreter regression uses pre-Genesis UTXO rules.
This session has no separate physical result for those cases. Completed mock transactions
also pass final unlocking-script execution, checking signature order as well as ECDSA validity.
The retained adapter rejects mixed P2SH/non-P2SH inputs. An input with multiple unsigned keys
owned by the same keystore may require another signing pass; partial results are intentional.

In the transaction window, **Sign** adds eligible signatures from this account. **Sign selected
inputs…** is available for Ledger-only accounts, lists eligible unsigned inputs, and preserves
all other slots. The device receives the complete transaction even when only one input is
selected: ALL still commits to every input and output. Incomplete transactions remain exportable
as signing JSON for the next cosigner. A completed transaction uses the ordinary separate
broadcast action.
