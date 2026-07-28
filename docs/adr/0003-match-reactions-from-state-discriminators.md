# Match reactions from state discriminators

Every concrete State Model has a unique enum-valued State Discriminator, which is the source of truth when selecting Reactions. Exact matching compares the discriminator and is the default; an explicitly inherited match resolves the registered model for that discriminator and includes its subclasses. This preserves reliable deserialization while allowing deliberate reuse of behavior through State Model inheritance.
