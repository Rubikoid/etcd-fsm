# Bound retry attempts with timeouts and backoff

Every user function invocation has a configurable timeout, and every explicit `Retry` uses a configurable exponential backoff with jitter and no maximum attempt count by default. A retry remains eligible only while its original State Revision and command call are current. Transition Claims are released during backoff so unrelated pending transition work can proceed, while the function's Processing Claim remains leased to its owner.

An invocation timeout requests cooperative coroutine cancellation. The runtime schedules a retry only after that invocation has actually stopped; Python cannot safely start a replacement while user code suppresses cancellation and may still perform external side effects. A non-cooperative timed-out invocation is reported as stuck, retains its claims, and degrades health without terminating the runtime.

Backoff counters reset after successful completion or a new State Revision and restart from their initial delay after process restart. Individual functions may override the processor and runtime defaults, and an explicit `Retry` may request a longer minimum delay.
