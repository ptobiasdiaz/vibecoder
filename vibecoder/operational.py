"""Pure derivation of operational state."""
from .domain import ExecutionCapacity, GitHubConnectivity, OperationalSnapshot, PublicReadiness


def derive_operational_state(github: GitHubConnectivity, execution: ExecutionCapacity, *,
                             administratively_paused: bool = False,
                             recovery_barrier: bool = False,
                             projects_available: bool = False,
                             eligible_work: bool = False) -> OperationalSnapshot:
    online = github is GitHubConnectivity.ONLINE
    ready = online and execution is ExecutionCapacity.AVAILABLE
    return OperationalSnapshot(
        github, execution, PublicReadiness.READY if ready else PublicReadiness.STANDBY,
        online, administratively_paused, recovery_barrier, projects_available,
        eligible_work,
        ready and not administratively_paused and not recovery_barrier
        and projects_available and eligible_work,
    )
