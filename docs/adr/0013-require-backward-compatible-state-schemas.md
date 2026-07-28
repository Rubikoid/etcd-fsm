# Require backward-compatible state schemas

Stored State Values use their Pydantic representation directly without an internal schema-version envelope or runtime migration pipeline. New library consumers must keep State Models backward-compatible with already stored values; incompatible changes require an external data migration before deployment. This avoids live schema mutation and keeps state decoding transparent.

## Consequences

Adding fields requires defaults or optionality, and renamed fields require an explicit compatibility strategy such as validation aliases. Removing or changing existing discriminator values is incompatible. A State Value that the current model cannot validate is a data or deployment error rather than an input for automatic migration.
