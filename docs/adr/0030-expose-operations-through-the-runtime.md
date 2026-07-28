# Expose client operations through the runtime

Every service that submits Command Calls also runs an FSM Runtime, so the library does not introduce a separate lightweight FSM Client. A Bound FSM obtained from the runtime exposes typed reads, scans, command submission, and result resumption alongside the runtime's local processor activation. This keeps connection, watch, and recovery lifecycle in one component per service.
