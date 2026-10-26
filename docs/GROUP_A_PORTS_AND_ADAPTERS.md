# GA-MOD-02 — Standalone ports and adapters (implementation contract)

**Scope:** pure Group A orchestration / ports, not live QDP processing.

## Module layering

```text
             HTTP / host app (currently task creation disabled)
                              │
              GroupAService / GASettings  <-- pure injected port boundary
                      │         │
       ┌──────────────┘         └────────────────┐
       ▼                                         ▼
GAPorts protocols                       v1 wire validators
       │                                  contracts_v1.py
  ┌────┼─────────────────────────────────────────┐
  ▼    ▼         ▼           ▼          ▼       ▼
 QDP  Media  Transcript    Vision    Writer   Queue / Handoff
 adapters (all unavailable and fail-closed until relevant stages)
                │
          Existing A1 repository.py
       **archived as runtime-off shim**
   (GA-MOD-03+ migration & GA-MOD-04 handoff)
```

GA-owned code:
- `ports.py`: eight explicit **Protocols**: `QDPReader`,
  `MediaCatalog`, `TranscriptReader`, `VisionReader`, `ResultWriter`,
  `HandoffConsumer`, `Clock`, `TaskQueue`. A `GAPorts` bundle
  requires all of them. No QDP/PSI implementation imports.
- `config.py`: `GASettings` (boolean flags) + exact parser of
  `CM_GROUP_A_ENABLED`, `CM_GROUP_A_QDP_HANDOFF_ENABLED`,
  `CM_GROUP_A_PSI_REUSE_ENABLED`. All default **false**. Even forcing
  all flags true cannot activate the unfinished handoff.
- `adapters/unavailable.py`: missing-adapter implementation with explicit
  `adapter_not_connected` capability; every attempted read, write or queue
  operation throws `PortUnavailable`. Frozen-clock testing uses an injected
  `Clock`. No network, filesystem, database, provider or hidden model calls.
- `service.py`: `GroupAService` + pure plan / capability negotiation.
  `inspect(pointer)` is always unavailable until the durable QDP inbox is
  installed. `validate_authoritative_pointer(pointer)` is an explicit
  **non-activating dry-run** for injected fake adapters: requires exact
  `qdp-handoff/v1`, authoritative re-read returning `ready`, and a
  verified `ga-media-manifest/v1` pinned to the same project/run/video/hash.
  It never writes or queues anything.
- `capabilities.py`: legacy HTTP capabilities response continues to expose
  `qdp_handoff: unavailable`, `runtime: disabled` and now includes
  `adapter_readiness` detailing every port (exact versions, reasons).
  Legacy HTTP task endpoints, workers and direct repository entrypoints stay
  fail-closed; no preemptive activation of A1 DB path.

## Port version table (strict equality)

| Port | Expected contract | Required for future runtime? |
|---|---|---|
| QDPReader | `qdp-handoff/v1` | Yes |
| MediaCatalog | `ga-media-manifest/v1` | Yes |
| TranscriptReader | `transcript/v1` | Optional reuse; ASR stages later |
| VisionReader | `visual-preparation/v1` | Optional reuse; OCR stages later |
| ResultWriter | `group-a-assembly/v1` | Yes |
| HandoffConsumer | `group-a-to-b/v1` | Yes |
| TaskQueue | `group-a-jobs/v1` | Yes |
| Clock | aware UTC datetime via `utcnow()` | Always injected |

`negotiate(port, observed_version)` returns `available/unavailable/incompatible`
with the expected version. `available` is a **claimed adapter capability**,
not authorization: execution still requires a working GA SQL/inbox,
authoritative QDP source verification and verified media bytes. No fallback to
an arbitrary newer/older peer version. `REQUIRED_EXECUTION_PORTS` is a
future target, not an assertion they are installed now.

## Fail-closed examples

```python
from intelligence.group_a import GroupAService
from intelligence.group_a.config import GASettings
from intelligence.group_a.adapters import disconnected_ports

ga = GroupAService()
assert ga.capabilities()["state"] == "unavailable"
assert not GASettings().enabled

# A future host will inject actual ports after GA-MOD-03/04; it cannot start
# runs simply by exporting CM_GROUP_A_ENABLED=1.
```

Test-only injected `FakeReader` and `FakeSink` in
`tests/test_group_a_ports.py` verify version negotiation, revoked readiness,
cross-project and changed media inputs, missing upstream imports and
**zero writes / zero tasks**. No fake adapter is installed in application
startup. The real QDP `input_sha256` remains the canonical field name.

## Remediation path and QA

- GA-MOD-03: implement actual GA-only SQL and ResultWriter/TaskQueue.
- GA-MOD-04: implement QDPReader and durable inbox + event receipt/ACK.
- GA-MOD-05: implement MediaCatalog read + on-disk byte digest verification.
- GA-MOD-08: introduce exact asset-hash PSI read bridge.
- GA-MOD-19: implement external Group B handoff.
- GA-MOD-20/21: wire orchestration runtime/host UI under deliberate feature flags.
- No step here silently turns legacy A1 `repository.work_once` back on.

```bash
python -m compileall -q intelligence/group_a tests/test_group_a_*.py
python -m unittest discover -s tests -p 'test_group_a_*.py' -v
```

CI installs pinned test-only `jsonschema==4.23.0` so v1 wire schemas
are fully checked in addition to syntax tests. The **GitHub Actions runner
backlog is not fixed by changing Python code**; see tracker for actual
run states. A code-implemented stage is marked separately from runtime QA.
