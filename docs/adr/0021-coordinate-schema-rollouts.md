# Coordinate shared schema rollouts

All independently deployed FSM Processors use a coordinated version of their shared FSM Schema. A new State Discriminator or incompatible command contract is not written until every processor has deployed code that understands it. Unknown discriminator values remain deployment or data errors rather than an ignored forward-compatibility case.

## Consequences

Schema packages are pinned and rolled out separately from enabling producers of new values, typically using a deployment barrier or feature flag. The runtime can keep strict complete enum-to-model registries and quarantine unknown stored values.
