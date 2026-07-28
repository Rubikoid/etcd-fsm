# Derive composite state keys from models

State Models and Command Models implement a shared structural protocol whose synchronous method derives a composite State Key from their identity fields. Keys may contain multiple path segments such as `dtc/{user_id}/{task_id}` for readable debugging and prefix scans. State Values remain stored at that path, with the single command slot stored at its `/rpc` child.

The runtime accepts only normalized relative paths, rejects empty, dot, parent, and reserved terminal segments, and verifies that a transition preserves the current key. A Command Model addresses its State Value through the same method, so command calls require no separate key argument.

## Consequences

Identity fields are present in every concrete State Model and relevant Command Model, normally through shared base models. Reads that do not already have a model still address data using its composite string key.
