# Type command results through model generics

Every concrete Command Model carries its public result type as a generic parameter in the shared FSM Schema. A shared result alias is reused as the command function's return annotation with `Retry` added for the non-completing path. Submitting the model returns `CommandHandle[ResultT]`, so independently deployed callers retain the exact completion type without importing the processor that implements the command.

Command functions express state changes as `Applied[StateModel]`; signature analysis extracts those models as graph targets. `Rejected[ErrorModel]`, `Ignored`, and `Delete` are also public result members, while `Retry` is never observable as a completed result.
