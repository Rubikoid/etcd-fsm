# Treat state and command models as immutable snapshots

State Models and Command Models inherit frozen Pydantic configuration and user functions must never mutate received instances. Every state change is represented by returning a separately constructed State Value and is then validated and committed as a transition. This keeps handler inputs stable across concurrent work and prevents mutation from bypassing graph and revision checks.

Pydantic freezing is shallow, so the runtime gives each invocation its own decoded input and detects mutation of nested containers by comparing it with the canonical pre-invocation snapshot. A mutated input fails the invocation and is never committed. Returned models are validated again through the FSM Schema rather than trusted merely because they are `BaseModel` instances.
