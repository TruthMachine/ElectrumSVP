# Ledger BCH compatibility history

Checked 2026-11-16. **OS version, device-app version, SDK build, host library and USB identity
are independent axes.** A version number is useful evidence, not a substitute for app identity
and capability checks. The primary target is current S Plus/X firmware; legacy paths must have
explicit tests and must not silently weaken signing assurance.

| Boundary / observation | Evidence | Adapter decision |
| --- | --- | --- |
| Historical `0x41` BCH path | BCH configuration has fork ID 0; device signing accepts `SIGHASH_ALL | FORKID` | Keep the BCH legacy protocol; do not route to modern Bitcoin PSBT |
| Legacy Nano S / OS 2.1.0 / BCH 2.4.7 | Our device reports these versions; public-key, display, rejection, transaction and message tests passed; trusted-parent partial/bare-multisig tests also passed. Upstream has a Nano S 2.1.0 / app 2.4.7 / SDK v22.2 build tag | Retained compatibility candidate; source tag is not attestation of the installed binary |
| BCH 2.4.9, 2025-09-08 | Changelog removes original Nano S builds | Do not require S Plus/X app versions on Nano S; these are different device generations |
| BCH 2.4.10, 2026-02-19 | Derivation permissions hardened; unrestricted master derivation removed for altcoins | Setup must not request the parent `m/44'`; probe a permitted full account path |
| BCH 2.4.11, 2026-04-13 | Adds GET_MASTER_FINGERPRINT | Useful for root identity, but **not** a replacement for the parent fingerprint inside a depth-3 account xpub |
| BCH 2.4.13, 2026-09-25 | Security changes include trusted-input key generation and change detection | Do not infer fee verification or trusted-input behavior solely from historical btchip examples |
| BCH 2.4.14 on S Plus and X | Our USB app identity and legacy version commands both report 2.4.14 | Direct physical behavior recorded; public master remains 2.4.13, exact source-build mapping requires further evidence |
| Application query serialization | Current official SDK serializes `B0 01 00 00 00`, including zero data length. A controlled Nano S / BCH 2.4.7 repeat accepted both four and five bytes | Use the official five-byte encoding. No version-based fallback or exclusion is justified by the earlier isolated timeout |
| Locked / unavailable states | Explicit `0x5515` observed on S Plus; an earlier scan enumerated nothing | Only an explicit lock response proves lock state. Do not classify absence, access conflict or timeout as locked |
| Identical serial fields | Physical Nano S and S Plus collided under the old ID construction | Include vendor/model family; disambiguate same-model collisions by path. Never choose by list position |

An initial five-byte query timed out after an app switch, then a later four-byte query succeeded.
The initial attribution to byte count was **retracted** after checking the actual official
serializer and repeating the experiment on 2026-11-16: Nano S / BCH 2.4.7 accepted three queries
of each length, in alternating order and across a reopened connection, in 24–26 ms each.
The matching SDK tag's default command dispatcher checks CLA/INS/P1/P2 without an exact
four-byte length condition. The earlier timeout's cause remains undetermined; app transition,
connection state and access conflicts must be investigated separately. There is no established
byte-count firmware boundary. This new app-identity check also cannot explain the owner's old
ElectrumSV coinbase failure without separate reproduction.

`app-bitcoin-legacy` and its pinned `lib-app-bitcoin` submodule must be considered together.
A deployment can report a version not yet present in master/changelog. Likewise an OS upgrade
can change USB/app-switch behavior without changing BCH's transaction rules. Preserve raw
status codes in diagnostics, inspect app identity after reconnect, and admit only signatures
that verify against the requested transaction/message.

Existing BIP32 test-vector regressions cover both unrestricted and hardened path behavior,
coin 0/145 account paths and xpub header preservation. Session tests cover app-query framing,
lock responses, rejection, duplicate identities and reconnect selection. Transport tests
exercise partial-response timeout and overlapping requests. These are host-side tests, not
firmware emulation or proof that every intermediate firmware release is supported.

Sources:

- [Legacy BCH changelog at reviewed master](https://github.com/LedgerHQ/app-bitcoin-legacy/blob/fa287121d830a9ea4518babd09993764d32439fb/CHANGELOG.md)
- [BCH app paths and fork ID](https://github.com/LedgerHQ/app-bitcoin-legacy/blob/fa287121d830a9ea4518babd09993764d32439fb/Makefile)
- [Device-side sighash handling](https://github.com/LedgerHQ/lib-app-bitcoin/blob/e369bc30a714b94a110b9f78f3e0569dcba83285/handler/hash_sign.c#L120-L133)
- [Official Ledger command flow](https://developers.ledger.com/docs/device-interaction/dmk-ts/beginner/exchange_data)
- [Actual official APDU serializer, including length byte](https://github.com/LedgerHQ/device-sdk-ts/blob/d6a7ee7e34c2de4d6000bcf6e922e6120e7da7c0/packages/device-management-kit/src/api/apdu/model/Apdu.ts)
- [Nano S 2.4.7 build tag](https://github.com/LedgerHQ/app-bitcoin-legacy/tree/nanos_2.1.0_2.4.7_sdk_lns-2.1.0-v22.2)
- [Matching SDK's default command dispatcher](https://github.com/LedgerHQ/ledger-secure-sdk/blob/36dd2110ad6349ab84da7bf9d33ecba5df9c4a19/src/os_io_seproxyhal.c#L782-L802)
- [CloseApp command and expected disconnection](https://github.com/LedgerHQ/device-sdk-ts/blob/develop/packages/device-management-kit/src/api/command/os/CloseAppCommand.ts)
- [Electron Cash restricted-path fix](https://github.com/Electron-Cash/Electron-Cash/pull/3265)

Bounded trusted-input parsing (including synthetic coinbase parents) is now implemented.
Nano S / BCH 2.4.7 physically passed 2-in/2-out P2PKH, partial signing and bare 2-of-2 multisig,
with existing signatures preserved and no unverified-input warning observed by the owner.
The current budgets are engineering policy, not measured device maxima. Modern S Plus/X need
these specific cases repeated. Contemporary vendor SDK adapters and a standalone BSV device
app are separate future work, not implied by version ranges.

Both the pinned 2.4.7 device code and public 2.4.13 code reject BCH sighashes other than `0x41`.
For installed 2.4.14 we conservatively infer the restriction; no negative device test or exact
source-to-binary mapping is claimed. This restriction is independent of partial input signing
and multisig, which can use `0x41`.
[2.4.7 exact handler](https://github.com/LedgerHQ/lib-app-bitcoin/blob/774dfd2daaf967a32ce112f23bff9262364e1d5e/handler/hash_sign.c#L120-L125).
