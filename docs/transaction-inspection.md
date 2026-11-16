# Transaction input inspection

An account name appears on an input only when its outpoint belongs to that account.
Missing amounts no longer sum to a misleading zero. The dialog reads missing previous
transactions from the existing connection, checks the transaction hash and output index,
and computes the fee once all input amounts are known. Requests are deduplicated with at
most four concurrent reads. Results stay in the dialog; they do not change wallet ownership,
UTXOs, signing data or database contents. Closing the dialog stops queued lookups. Offline,
timeout and invalid-response cases retain unknown values and offer Retry. The previous
transaction can be inspected from the input once fetched. Nonstandard script display is
also updated for the project's bitcoinX 0.9+ API.


Regression coverage uses synthetic transactions and a simulated server, not a funded wallet.
Translations are submitted separately.
