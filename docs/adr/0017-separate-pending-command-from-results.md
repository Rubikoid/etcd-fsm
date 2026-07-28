# Separate the pending command slot from retained results

For a composite State Key, the single pending Command Call is stored at its `/rpc` child and a completed Command Result is stored at `/rpc/{call_id}` with a bounded retention lease. Completion atomically compares the relevant state and command revisions, commits any next State Value, removes the pending `/rpc` key, and writes the correlated result. The slot is therefore immediately available for another command even when the previous caller is disconnected.

The `rpc` segment is reserved and cannot appear in a model-derived composite key.
