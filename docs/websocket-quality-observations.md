# WebSocket quality: same-process observations

The application adds passive metrics only. It does not change reconnect scheduling,
WebSocket timeouts, DB state or session ownership. No client traffic is generated.

`chargeops_csms_recovery_outcomes_total{kind="service",scope="pod_local",result="success|timeout"}`
counts resolved closed-session observation cohorts, **not client reconnect attempts**.
A successful recovery means the same ID was accepted by this process within
`WS_RECOVERY_TIMEOUT_SECONDS` (default 60 seconds) after its close was detected.
The observation deadline is not the client's connection timeout.

`chargeops_csms_recovery_seconds` is a histogram of close detection to accepted
reconnect, including the client's backoff. It includes successful recoveries within
the observation deadline only. Timeouts are excluded from latency samples and
must be read alongside recovery success percentage and sample count.

Limitations:

- Cross-Pod recoveries are not tracked. A recovery elsewhere can become a local timeout.
- Process restart/shutdown loses pending observations. Planned drain and fixtures do
  not create new cohorts. These metrics cannot validate recovery from a Pod restart.
- A replacement session followed by the old session's delayed close must not create
  a phantom pending recovery; session UUID matching enforces this.
- Pending observations are capped at 10,000. Evictions increment
  `chargeops_csms_recovery_discarded_total`; affected inspection windows are unverified.
- No charger IDs are metric labels. Pending cohorts have not yet succeeded or timed
  out and are excluded from the resolved-cohort denominator.
- DB `charger_session` stores current ownership, overwriting on open and deleting on
  close. `status_history` stores state transitions. Neither stores complete client
  reconnect attempts, nor exact revision-specific reconnect latency samples.

GitOps contains the separate `websocket-session-quality` dry-run Analysis metric.
Its thresholds are intentionally unset until load-test review. Existing gates and
abort policies remain unchanged. Dashboard v18 uses this observation contract.
