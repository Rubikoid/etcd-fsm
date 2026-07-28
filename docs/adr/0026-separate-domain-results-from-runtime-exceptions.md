# Separate domain results from runtime exceptions

Expected negative command outcomes are ordinary `Rejected[ErrorModel]` members declared by the shared Command Model's result type. Dispatch failures, missing state, cancellation, expired results, and runtime or storage failures use typed library exceptions instead. This keeps `CommandHandle[ResultT].result()` honest: successful completion returns exactly `ResultT` without hidden framework result variants.

Submission raises `Busy` before creating a handle when the command slot is occupied. A local wait timeout raises an exception that retains the stable Command Handle and does not cancel durable work.

An unexpected exception from a command function completes that Command Call with a serializable `CommandExecutionError` and frees its slot without terminating the FSM Runtime. An unexpected exception from a Reaction is reported through the runtime error hook, retains its Processing Claim to prevent an immediate replica-wide crash loop, and degrades processor health until the claim is lost or processing is explicitly reset.
