# Known Issues

These are observed limitations of this experimental integration, not a list of
certified maximum device capabilities. See [validation.json](validation.json) for
versions and [Ledger measurements](ledger-benchmarks.json) for individual runs.

## Nano S Plus can lock during a long signing operation

Nano S Plus OS 1.7.0 / Bitcoin Cash 2.4.14 returned `0x5515` during three
512-input attempts: once after parent verification and twice while preparing
inputs 427 and 426 for signing. The owner confirmed a PIN screen. One failed
attempt was after the owner selected a 10-minute lock interval; we did not read
the setting or the device's timer origin programmatically. The exact reason for
that timing remains unresolved. It is not evidence of an input-count or RAM limit.

A subsequent run, started promptly after a fresh unlock with the owner-reported
10-minute setting, **completed all 512 signatures**. The owner confirmed no
unverified-input warning. Total elapsed time, including review, was 487.5 seconds.
Nano S and Nano X also completed 512-input runs. S Plus signed faster than Nano S
in these fixtures; the interrupted runs do not establish weaker hardware.

The wallet reports progress and an explicit locked-device error. It does not
disable auto-lock, send fake user activity, or silently change verification mode.
Unlock on the device and retry. A failed operation leaves the original transaction
and any existing cosigner signatures unchanged; there is no automatic resume of
the interrupted device session. Keep the device attended. Any temporary security
setting change is the owner's choice and should be restored afterward.

We have **not added a 400-input signing cutoff**. The full-parent work budgets
remain resource policies that can trigger an explicit reduced-verification choice,
not a promise that larger signing jobs will finish. Progress describes completed
work and time elapsed; it cannot predict an imminent device lock.

## Large-parent and long-running transactions need separate measurements

The scaling fixtures use distinct synthetic 128-byte coinbase parents, two outputs,
1000 nominal satoshis per input and a 100-satoshi nominal fee. The 512-input run
therefore streams 64 KiB of parents. This is not a test of 512 large real mining
transactions, many outputs, long scripts or unusual payout scripts.

Parent bytes, script lengths and USB command count affect verification time.
Turning parent verification off still sends the current transaction and still
requires a signature for each requested input; it does not make a large job instant.
Early timing records cover only parent and signing commands. They exclude current
transaction preparation and user review, so their sum is **not total signing time**.

Two early Nano S attempts (4 and 16 inputs) ended without a completed host result;
the owner's approval alone was not counted as a pass. A later 4-input retry and
64, 161, 256 and 512-input runs completed and were cryptographically verified.

## Coverage still outstanding

- P2SH multisig on all models. Partial and bare 2-of-2 multisig passed on all three tested Ledgers.
- Physical Trezor, KeepKey and BitBox01 tests. Their restored adapters retain old SDK pins.
- Same-model device ambiguity, real Ledger Wallet contention and packaged GUI hardware signing.
- Original reported mining-reward spend failure: no failing real transaction was provided.
- Exact public source mapping for installed BCH 2.4.14; the inspected legacy changelog ends at 2.4.13.
- Native PSBT import/export and non-`0x41` Ledger BCH sighashes are not implemented.
- No funded transactions or broadcasts were authorized or performed in these tests.

The inherited full software suite also has baseline failures; focused regression
success does not mean the complete suite passes. See [hardware-wallets.md](../hardware-wallets.md).
