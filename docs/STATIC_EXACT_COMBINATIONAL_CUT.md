# Static exact combinational-cut mode

## Status and claim boundary

The production flow remains `sequential-only`. Phase 3 transports register
outputs, transport-safe register inputs, and replicated primary inputs; other
combinational connectivity remains atomic. The checked-in
`combinational-cut characterize` command is read-only. It identifies a
conservative LUT-only eligibility upper bound, combinational SCCs, potential
cut dependencies, and atomic-component reductions. It does **not** change a
partition, create a transport schedule, establish macro-cycle equivalence, or
claim physical timing closure.

The intended opt-in mode is `static-exact-combinational`. It will be enabled
only after the producer and an independent validator exist at every affected
Phase 3--7 boundary. Merely adding `combinational` to the existing legal-cut
constant is invalid because the current schedule and equivalence model do not
prove when a downstream combinational value becomes available.

## Slot-edge convention

All exact-mode artifacts use `fabric-rising-edge-current-slot/v1`:

- A TX assigned slot `S` samples its source at the fabric rising edge for
  which the controller's pre-edge value is `S`.
- An RX assigned arrival slot `A` updates its shadow register at the fabric
  rising edge for which the pre-edge value is `A`.
- A value captured or architecturally launched at edge `E` with a declared
  combinational budget of `B` slots is first eligible for downstream sampling
  at edge `E+B`. Consequently a one-slot relay budget requires
  `next_tx_slot >= arrival_slot + 1`.
- The virtual DUT commits at the rising edge whose pre-edge slot is
  `frame_slots-1`. A capture value may become ready at that edge; its physical
  delay budget must include setup/uncertainty, so no unmodelled setup window is
  implied.
- A transport arrival itself remains strictly before the commit slot, matching
  the existing runtime barrier contract.

This convention matches the current generated TX combinational mux, RX
`always_ff` shadow capture, relay `arrival+1` rule, and frame-barrier
virtual-clock enable. Scheduler, independent validator, RTL tests, and
Phase 7C must consume the same versioned convention rather than defining
local `+1` rules.

## Planned semantic contract

Exact mode will bind one versioned sub-contract through assignment, routes,
schedule, Phase 6 split, and Phase 7C:

```json
{
  "schema": "emuflow.static-exact-combinational-cut/v1",
  "mode": "static-exact-combinational",
  "max_cross_fpga_dependency_depth": 1,
  "comb_segment_budget_slots": 1,
  "slot_edge_convention": {
    "id": "fabric-rising-edge-current-slot/v1"
  },
  "cut_nodes": [],
  "dependency_edges": [],
  "logic_segments": [],
  "capture_requirements": [],
  "metrics": {},
  "source_identity": {}
}
```

Functional dependency and capture coverage come from complete EmuIR
connectivity. TimingPathDB associates delay and QoR evidence, but a truncated
timing-path sample can never define functional coverage.

## Conservative eligibility policy

The first version is intentionally fail-closed:

- only single-driver, acyclic, mapped LUT soft logic is potentially cuttable;
- FF/memory state, DSP/carry/memory cascades, clock/reset, multi-driver nets,
  latches, opaque primitives, asynchronous controls, and combinational SCCs
  remain atomic;
- top-level output capture and supported synchronous FF data/control inputs
  are valid terminal boundaries;
- every reconvergent predecessor is retained;
- the dependency-depth limit is reconstructed from EmuIR after each candidate
  assignment, independent of the partition provider.

The characterization report is an upper bound because it ignores capacity,
user group/fixed constraints, BoardDB hop limits, link capacity, schedule
feasibility, and physical segment deadlines.

## Delivery sequence

1. **Characterization (implemented, no behavior change).** Read-only SCC,
   eligibility, dependency-depth, and theoretical atomic-component report;
   independent exact replay; tamper tests.
2. **Phase 3 depth 1.** Explicit cut policy, assignment semantic contract,
   provider-independent legality reconstruction, and balance fixture. Its
   strongest status is `partition-legality-pass`.
3. **Phase 4/5 depth 1.** Exact contract propagation, deterministic
   dependency-aware list scheduling, source-ready/capture certificate, fixed
   frame fail-closed diagnostics, and tamper tests.
4. **Phase 6.** Event-driven macro-cycle simulation and exhaustive/formal
   small-design equivalence without reading reference final net values at TX.
5. **Phase 7C.** Routed `launch_to_tx`, `rx_to_tx`, and `rx_to_capture`
   deadlines plus global target-clock and virtual-runtime WNS/TNS.
6. **Depth 2 and optimizer integration.** Path-local readiness precedes any
   timing-DAG/ratio provider promotion.

## Commands

```bash
emuflow combinational-cut characterize \
  --ir build/phase1/design.emuir.json \
  --depth-limit 1 --depth-limit 2 \
  --output build/comb-cut/characterization.json

emuflow combinational-cut validate \
  --ir build/phase1/design.emuir.json \
  build/comb-cut/characterization.json
```

Characterization is deterministic and near-linear apart from sorting. The
validator reconstructs the complete report from EmuIR and rejects changed
eligibility, SCC, dependency, depth, source identity, or metric fields.
