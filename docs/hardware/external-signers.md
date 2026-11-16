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

## PSBT boundary

The existing incomplete-transaction JSON and this envelope are not BIP174/370 PSBT.
No PSBT reader/writer or device adapter is introduced here. A future bridge must preserve
values, scripts, key origins, signatures and BSV sighash semantics, and reject fields it
cannot map without loss. Hardware/UI integration is a separate change.
