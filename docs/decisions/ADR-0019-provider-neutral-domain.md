# ADR-0019: Provider-neutral domain

Status: accepted.

Projects and agents do not reference provider accounts. Plugins describe
capabilities; configured workers supply execution capacity. Provider session and
execution references are opaque to the core. An agent may exist without a worker,
and changing worker never changes logical agent identity.

