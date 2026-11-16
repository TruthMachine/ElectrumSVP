# Profile and startup integrity

Proof/header readers use the selected profile instead of silently reading another wallet
profile's cache. This keeps the selected-chain decision attached to the intended data directory.
Offline startup skips the header network check. Malformed remote responses are network-check
failures and cannot replace a valid local headers file. The existing online chain-mismatch
policy is unchanged. These adjacent fixes are covered by profile isolation and header-startup
regressions. The normal default wallet directory and original wallet opening flow remain.
