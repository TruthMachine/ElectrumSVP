# External BSV signing contract, version 1

The public Python seam is `electrumsv.devices.signing`. It imports no Qt, vendor SDK,
USB implementation, database or networking code. An adapter declares `SigningCapabilities`
and implements `ExternalSigner.sign(SigningRequest) -> SigningResult`. It never broadcasts.

## Preparing and admitting signatures

```python
from electrumsv.devices.signing import prepare_signing_request, apply_signing_result

request = prepare_signing_request(
    tx, tx.context, signer_owns_public_key, signer.capabilities,
    input_indexes={1},                     # zero-based; omitted means all eligible inputs
    sighashes={(1, 0): 0x41},               # (input, key slot); default is 0x41
)
result = signer.sign(request)
apply_signing_result(tx, request, result)
```

The request contains a detached transaction snapshot, explicit `(input_index, key_index,
public_key, sighash)` targets and a content-derived request ID. Ownership must be checked
against the adapter's account path and extended public key before signing. A caller-provided
public key or transport ID does not prove ownership.

The commitment covers version, locktime, outpoints, sequences, input values, signing scripts,
thresholds, public keys and outputs. Accumulated signatures are deliberately excluded so
independent cosigners can return in either order. A changed transaction intent invalidates the
request. The host verifies every existing and returned signature with its own sighash, rejects
unknown/duplicate targets and signature replacement, and applies a valid response atomically.
A result may contain only a subset of the requested signatures. It may not mutate the transaction.

`SigningRequest.to_dict()/from_dict()` and `SigningResult.to_dict()/from_dict()` define the
JSON envelopes. Request format is `electrumsvp-signing-request`, version `1`, chain `BSV`.
The transaction payload is SVP's existing signing JSON, with raw previous transactions in
`prev_txs`; targets carry hex public keys. Result format is `electrumsvp-signing-result`,
version `1`, and contains the request ID and signatures as DER + one sighash byte, hex encoded.
The result must be admitted against the **locally retained request**, never a request supplied
by the responding signer. Transport adapters must bound incoming message size before decoding.

The interface allows the six defined BSV ForkID combinations (`41`, `42`, `43`, `c1`, `c2`,
`c3` hex) only when an adapter declares them. SINGLE requires a corresponding output. This is
host validation support, not a promise that any particular hardware app implements these modes.
Arbitrary scripts still need an explicit script-code/ownership model and a capable device;
the present input types are the ones represented by SVP's transaction metadata.

## Ledger scope

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

## PSBT boundary

SVP's existing incomplete transaction JSON and this envelope are not BIP174/370 PSBT. No native
PSBT reader/writer is added by this patch. The modern Ledger **Bitcoin** app's PSBTv2 interface
is a different signing route and does not turn it into the BCH/BSV `0x41` adapter. A future PSBT
bridge must preserve input values, scripts, key origins, partial signatures and BSV sighash
semantics, and reject fields/scripts it cannot map without loss.

Sources: [2.4.7 device restriction](https://github.com/LedgerHQ/lib-app-bitcoin/blob/774dfd2daaf967a32ce112f23bff9262364e1d5e/handler/hash_sign.c#L120-L125),
[2.4.13 device restriction](https://github.com/LedgerHQ/lib-app-bitcoin/blob/e369bc30a714b94a110b9f78f3e0569dcba83285/handler/hash_sign.c#L120-L125),
[modern Bitcoin SIGN_PSBT](https://github.com/LedgerHQ/app-bitcoin/blob/develop/doc/bitcoin.md#sign_psbt),
[BIP174](https://bips.dev/174/).
