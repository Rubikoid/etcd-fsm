# Delete state through typed outcomes

A Transitioning Reaction or command function may include the library `Delete` type in its return annotation and return that outcome to remove the current State Value. Signature analysis represents it as a deletion edge in the Composed Graph, and commit atomically compares the source State Revision before deleting the state key. There is no direct graph-bypassing deletion API.

Deletion removes the State Value and its complete `/rpc` subtree without writing a tombstone. The library does not distinguish a deleted key from one that never existed and does not enforce a permanent prohibition on later initialization.

For command functions, `Delete` is also the correlated Command Result stored in the runtime's external control namespace with a bounded retention lease. This result is not object state and exists only so a disconnected caller can resume waiting. Leased claims expire independently.
