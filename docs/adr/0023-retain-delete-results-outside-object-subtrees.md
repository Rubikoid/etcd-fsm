# Retain delete results outside object subtrees

A `Delete` outcome removes all object-owned keys, including the current State Value and its `/rpc` subtree, and leaves no tombstone. When deletion is initiated by a Command Call, its correlated `Deleted` result is written atomically under the runtime's external result namespace with a bounded TTL. This preserves resumable request-response semantics without retaining data below the deleted object's key.
