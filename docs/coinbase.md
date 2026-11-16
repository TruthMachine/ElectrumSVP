# Coinbase lifecycle patch

This patch is independent of vendor SDKs and hardware transport. The owner's original symptom
was a visible reward that could not be constructed/signed for transfer; no failing real
transaction or error was supplied, so that historical root cause remains unproven.

## State and eligibility

Raw transaction parsing identifies a coinbase by its null outpoint. Wallet outputs retain
`IS_COINBASE`, independently of output index: a miner's reward at output 2 is still coinbase;
block position 0 is a separate fact. Existing indexing code already set this output flag; the
new regression protects it. No database migration or retrospective user-database rewrite is
performed here.

Maturity is relative to the **selected chain tip**, not a stored confirmation counter.
The wallet snapshots its chain, height and tip hash, then checks that the reward's merkle proof
matches the header at H on that chain. Only then does `tip.height + 1 >= H + 100` establish
eligibility for the next block. Same-height or longer forks missing that reward fail membership
immediately, even before queued reorg cleanup; a fork after the reward's common-ancestor block
can preserve membership. A shorter selected tip can make a previously mature reward immature. Balance categorization and coin selection use the same boundary.
Ordinary zero-confirmation outputs remain selectable unless the user selects confirmed-only
funds. This includes zero-confirmation descendants of a mature, verified reward. Only known
invalid/immature reward ancestry blocks those outputs; confirmation is not required globally.

A reported positive height alone is not a merkle proof. Unknown/zero/negative heights cannot
become spendable simply because the local tip is high. Construction and signing recheck known
reward ancestry, including when a previously selected coin survives a tip change.

On chain reorganization, affected settled and cleared entries lose position, verification
state and proof bytes in the database as well as in memory. A reward removed from every
account's subscribed key history is similarly invalidated, including after an offline reorg.
Removal from just one key does not invalidate a reward still reported for another key/account.
A later height notification does not reuse the old proof. Late proof replies must match both
the current block header and the current requested transaction height.

Offline checks resolve the saved tip by hash and height; a saved height alone is insufficient.
There is no silent substitution of the global longest chain. Saving the tip now calls the
bitcoinx `Chain.tip()` method and writes display-order hash text consistently. Legacy reversed
hash storage is accepted only if it resolves to a known header at the saved height. Missing
proofs are requested again even if an old block position remains in the database.

Transaction and output records are retained for inspection/reconfirmation. Known unsettled
descendants of an invalidated or immature reward cannot supply spendable outputs. Invalidated
reward/descendant deltas do not inflate the history running balance. History labels a reward
without a verified block explicitly; it is not shown as an ordinary mempool payment. Absence
is not labelled proof of permanent orphaning: the reward can be proved on the current chain
again, then must meet that height's maturity boundary.

## Evidence and limits

`test_coinbase.py` exercises output-index recognition, maturity at 99/100 confirmations, tip
rollback, ordinary zero-confirmation selection and mature-reward zero-confirmation descendants,
real temporary SQLite proof invalidation/reload, changed height, reconfirmation,
retained descendants, selected-coin rejection, subscription removal, selected-tip fork/rollback
checks, offline tip identity, and late proof replies
from superseded blocks. Native proof export
uses the invalidated wallet proof cache. Existing standalone BREAD/BEEF JSON caches and third
party exported proof packages are not retroactively rewritten; their current-chain validity
must be checked when consumed.

This does not replace full-node consensus validation or prove unknown external inputs are
unspent. Server-side indexing, arbitrary mining payout scripts, conflict resolution for
unrelated transactions, and the original reported failing transaction remain outside the
specific regression evidence. No owner wallet has been reindexed and no reward has been spent.

Startup/profile isolation and translations are submitted separately.
