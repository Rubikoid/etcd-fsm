# Allow one transitioning reaction per concrete state

Any number of Observer Reactions may match a concrete State Discriminator, but no more than one matching Transitioning Reaction may propose its next State Value across the Composed Graph. Each FSM Processor validates its local registrations after applying exact and inherited reaction matching, and composition tests validate the union of independently deployed processors. This prevents competing external actions from racing to produce different transitions while retaining independent observers such as audit and metrics.

## Consequences

Observer Reactions can return only `Retry` or `None`. A Transitioning Reaction can additionally return a State Value, which is still committed using an optimistic compare against the State Revision it processed.
