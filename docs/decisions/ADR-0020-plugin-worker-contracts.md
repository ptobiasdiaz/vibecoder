# ADR-0020: Segmented plugin and worker contracts

Status: accepted.

Installation, authentication, probing, runtime, observation, and cancellation
are separate protocols. Unsupported operations fail with a typed capability
error and are never emulated silently. Worker selection is deterministic,
honours pins and continuity, and refuses reassignment after an UNKNOWN result.

