# EmuFlow architecture and implementation plan

## 1. Goal and scope

EmuFlow compiles a synchronous RTL design into multiple FPGA implementations
connected by statically scheduled board links. The target is a source-complete
implementation through synthesis, partitioning, system routing, TDM, pin
planning, placement, and FPGA routing. Every default engine must be editable
source in this repository and built from the repository root.

The default research target is a public VTR academic architecture. The same
provider-neutral artifacts support later ECP5 and UltraScale+ adapters.
Vivado is an optional UltraScale+ comparison/sign-off and bitstream backend;
it cannot satisfy the default open-flow completion gate. The current open
gates are additional architecture mapping profiles and post-placement timing
back-annotation. Public VTR TimingDB-to-OpenSTA translation and VPR detailed
routing are implemented. The authoritative machine-checked inventory is
`SOURCE_MANIFEST.json`.

The initial semantic envelope is intentionally narrow:

- one virtual DUT clock;
- synchronous reset;
- partition cuts only at register outputs or primary inputs;
- combinational strongly connected components remain within one FPGA;
- hard/vendor IP is represented as a fixed, indivisible macro;
- board links use deterministic static schedules;
- a global barrier completes before each virtual DUT clock-enable.

Multi-clock designs, arbitrary combinational cuts, runtime packet switching,
partial reconfiguration, and transparent encrypted-IP partitioning are later
extensions.

## 2. Architectural layers

```text
RTL / IP / constraints
        |
        v
Global synthesis and EmuIR import
        |
        v
Sequential clustering and multi-resource partitioning
        |
        v
Board-level system routing
        |
        v
TDM scheduling and logical lane assignment
        |
        v
Per-FPGA netlist + transport RTL generation
        |
        v
Provider-selected technology mapping and mode-aware packing
        |
        v
Root-built VPR exact architecture packing
        |
        v
Root-built OpenPARF clustered placement
        |
        v
Root-built VPR routing-resource graph and detailed routing
        |
        v
Open routed physical artifact
        |
        +---- optional real-device backend / Vivado comparison and bitstream
```

The layers communicate through explicit, versioned artifacts rather than
sharing tool-internal data structures.

## 3. Core data models

### 3.1 EmuIR

EmuIR is the board-independent logical hypergraph. It preserves stable
hierarchical names, cell types, primitive parameters, directed net endpoints,
resource vectors, clock/reset classification, and partition-cut eligibility.

The checked-in Phase 1 representation is JSON for inspectability. A Cap'n Proto
encoding can be added later without changing the logical schema.

### 3.2 BoardDB

BoardDB describes FPGA nodes and board links. A virtual BoardDB may omit all
package pins while still providing topology, lane count, frequency, direction,
and latency. A hardware board support package later adds connector, bank,
package-pin, reference-clock, and shell-DCP bindings.

Logical lane assignment and physical pin binding are separate stages:

```text
cut signal -> board link -> TDM slot -> logical lane -> package pin
```

Only the final arrow requires a real board.

### 3.3 FPGA Interchange

FPGA Interchange is the single-FPGA physical boundary. It carries device
resources, the mapped logical netlist, placements, pin mappings, and routing.
It does not represent multi-FPGA topology or TDM and is therefore not the
system-level IR.

## 4. Build DAG and cache boundaries

```text
global/design.emuir.json
  -> clustering/clusters.json
  -> partition/assignment.json
  -> system/routes.json
  -> system/tdm_schedule.json
  -> system/lane_map.json
  -> fpga_N/design.netlist + design.xdc
  -> fpga_N/packed.phys
  -> fpga_N/openparf/result.pl
  -> fpga_N/placed.phys
  -> fpga_N/routed.phys
  -> optional/fpga_N/routed.dcp
  -> optional/fpga_N/design.bit
```

Every artifact records a schema version and upstream inputs. A later runner
will hash inputs so selecting a real board only invalidates topology-dependent
stages; global synthesis and logical analysis remain reusable.

## 5. Implementation phases

### Phase 1 — Board-independent frontend (implemented)

Deliverables:

- EmuIR v1 model and validator;
- Virtual BoardDB v1 model and validator;
- Yosys JSON importer;
- provider-neutral LUT/FF plus hard-resource classification, with
  vendor-specific extensions isolated behind adapters;
- CLI and Phase 1 report;
- virtual `xcvu3p` two-FPGA platform;
- deterministic regression fixture and unit tests.

Acceptance:

- malformed IR/platform input is rejected with actionable errors;
- the example imports as four LUTs and four FFs;
- nets are classified as clock, reset, register-output, primary-input, or
  combinational;
- the design is compared with effective per-FPGA capacities;
- all tests run with Python 3.9 and no external packages.

### Phase 2 — Provider-neutral physical architecture and placement

The default open increment now implements:

- a C++17 VTR architecture XML importer;
- deterministic auto-layout expansion into ArchitectureDB;
- heterogeneous LUT, FF, carry, multiplier, memory, and I/O capacities;
- a provider-neutral Architecture TimingDB containing primitive and block
  arcs plus routing switches, segments, and directs;
- a pinned and SHA-256-verified public VTR flagship model; and
- independent architecture/timing validators.

The ArchitectureDB capacity policy takes the maximum primitive count across mutually
exclusive VTR modes. It is suitable for early global placement capacity, but
not exact packing legality by itself. The source-built VPR backend consumes
the original XML and performs exact mode-aware packing. A C++ importer now
publishes those clusters in a hash-bound contract without flattening mode,
pb-hierarchy, or atom-membership decisions. Those clusters are exported as
OpenPARF Bookshelf resources using exact VTR site capacities. OpenPARF
performs analytical placement and single-site min-cost-flow legalization; an
independent checker verifies completeness, compatibility, capacity, and
collisions before emitting VPR `.place`.

For the pinned public flagship profile, Yosys techmaps inferred multiplier
and synchronous RAM cells into the exact VTR model ports and legal modes.
VTR's bit-sliced RAM atoms remain visible until VPR packs them into a physical
memory block. ArchitectureDB dimensions are derived from VPR's auto-layout
placement header so the OpenPARF and VPR device views cannot silently diverge.
The `vpr fpga-open` orchestration command executes these contracts in order,
rejects stale output directories, and writes one aggregate report only after
all independent checks pass.

The earlier UltraScale+ risk spike remains an optional backend and implements:

- ArchitectureDB v1 and Placement v1;
- a hash-bound physical-region sidecar with exact SLR/clock-region coverage,
  package-specific I/O-bank inventory, and an independent checker;
- Vivado Site/BEL inventory to ArchitectureDB;
- EmuIR to OpenPARF Bookshelf;
- automatic execution of root-built OpenPARF;
- OpenPARF `x/y/z` result to legal UltraScale+ Site/BEL placement;
- one-instance/one-BEL, compatibility, completeness, and collision checks;
- LOC/BEL XDC generation;
- a real `xcvu3p` Vivado DCP/route validation harness.

The initial compatibility policy exposes only `*6LUT` and primary `*FF` BELs.
This is intentionally conservative: it avoids accepting a placement that
requires LUT input sharing or control-set repair that the flow does not yet
implement.

The optional UltraScale+ acceptance target remains in progress. It uses public
`xcvu3p` FPGA Interchange collateral to implement:

- DeviceResources to cached ArchitectureDB;
- fixed IO/clock/macro placement import;
- detailed pin mapping and intra-site routing repair;
- placed physical-netlist validation;
- RapidWright conversion to placed DCP;
- Vivado routing baseline.

Acceptance:

- an existing mapped `xcvu3p` design survives
  `FPGAIF -> OpenPARF -> FPGAIF -> DCP`;
- all cells have legal Site/BEL assignments;
- Vivado can route the placement without invoking its placer;
- name and logical/physical pin mappings remain consistent.

This phase is deliberately early because site packing, BEL pin permutation,
and intra-site routing are the largest physical-backend integration risks.

### Phase 3 — Sequential clustering and partitioning (implemented)

Implement:

- combinational SCC detection;
- sequential-cone clustering;
- carry, DSP, BRAM/URAM cascade grouping;
- fixed/group constraints for hard IP and board shells;
- TritonPart adapter with multi-dimensional resource weights;
- in-tree RePart multilevel partitioning with legal LUT replication;
- topology-aware refinement and utilization headroom.

Acceptance:

- every primitive belongs to exactly one partition;
- all group and fixed constraints hold;
- no forbidden combinational cut exists;
- every FPGA satisfies its effective resource capacities;
- cut and timing metrics are reproducible for a fixed seed.

OpenROAD/TritonPart is the default Phase 3 provider. EmuFlow exports each
legality-preserving atomic cluster as a hypergraph vertex weighted by cell
count and active FPGA resources, exports register-output nets as weighted
hyperedges, maps fixed constraints, executes TritonPart's multilevel
partitioner, and imports the solution into the common assignment schema. The
dependency-free greedy provider remains available explicitly as a fallback
and A/B baseline.

The RePart replication provider adds a versioned replication artifact without
changing the unique-owner primary assignment. A C++-kernel replicability mask
prevents stateful or unsupported vertices from entering the replication move
queue. Independent checks prove mapped LUT-only, acyclic, fanin-closed replica
clusters; charge every copy to target-FPGA capacity; recompute effective cut
demands; and require Phase 6 to materialize and cycle-check the copies.

Vivado/OpenSTA-derived timing weights, topology-aware repartition feedback,
and resource-specific heterogeneous FPGA capacity ratios remain QoR
extensions.

### Phase 4 — Board-level route/TDM co-optimization

The source-built C++ router operates over BoardDB with:

- unicast paths and multicast trees;
- per-direction link capacity;
- link latency and unavailable-link constraints;
- rip-up/reroute with historical congestion;
- infeasibility diagnostics.

Acceptance:

- every cut net reaches all sinks;
- no link exceeds modeled capacity;
- route trees contain no cycles;
- the checker independently reconstructs link utilization.

The `native-load-balanced-v1` mode uses the same C++ kernel without requiring
STA input. Four-FPGA diamond, multicast, unavailable-link,
infeasible-capacity, and half-duplex tests cover non-trivial topology cases.

The comparison `timing-aware-load-balanced-v1` provider adds an in-tree C++17
TLR/TRR kernel based on the routing portion of Chen et al., ASP-DAC 2026. Its
versioned `emuflow.sta-paths/v1` input carries clock domain, period, slack,
fixed delay, ordered cut signature, and cut-net sequence. The adapter:

- normalizes slack across clock domains using the paper's definition;
- losslessly compresses paths only when clock normalization and ordered cut
  behavior are equivalent, retaining the largest-fixed-delay representative;
- derives a fixed predicted delay table from BoardDB or explicit per-link
  overrides;
- distinguishes cable and SLL-class links; and
- represents flexible shared physical direction groups as half-duplex
  BoardDB links.

The C++ kernel applies majority-flow direction locking, timing-aware demand
ordering, criticality/utilization/history-weighted Dijkstra multicast trees,
negotiated congestion, and worst-path selective rip-up/reroute with
accept/rollback. Python does not reproduce the optimization. Its independent
checker reconstructs every tree, capacity domain, direction lock, route
delay, compressed-path signature, slack, and normalized slack from the
returned artifact.

The default `timing-aware-route-tdm-cooptimized-v1` provider extends that
kernel with the routing/TDM coupling used in the DAC 2020 and ASP-DAC 2021
co-optimization formulations. During tree construction it estimates each
capacity domain's quantized serialization ratio from signal load and physical
lane count, charges the resulting wait to timing-critical paths, and uses the
estimated TDM normalized slack as the first lexicographic rip-up/reroute
objective. Phase 5 then solves the exact per-hop KKT ratios and concrete
lane/slot schedule. The Phase 4 checker independently reconstructs the proxy;
the Phase 5 checkers remain the exact acceptance gate.

For real STA input, `emuflow sta emit-vivado-cut-map` produces a lossless
UTF-8-hex map from stable EmuIR cut-net IDs to the deterministic
`__emuflow_net_<index>` names in emitted mapped Verilog.
`scripts/vivado/export_cut_timing_paths.tcl` queries Vivado timing-path
objects that traverse those nets and exports clock group, requirement, slack,
data-path delay, and exact cut-net membership. `emuflow sta
import-vivado-tsv` converts that result to `emuflow.sta-paths/v1`; no
human-readable timing-report scraping or heuristic name matching is used.

The route/TDM provider is selected automatically when a timing-path artifact
is supplied. With no STA artifact, `native-load-balanced-v1` runs the same
source-built C++ kernel without timing criticalities.

### Phase 3--5 checked feedback loop

`emuflow cross-stage optimize` treats partitioning, route/TDM
co-optimization, and exact TDM scheduling as one candidate transaction. The
incumbent schedule is converted into checked channel-pressure net weights,
the source-built partitioner generates a new assignment, and the frozen
partition-independent STA database is projected onto that assignment before
the Phase 4 and Phase 5 kernels run.

Acceptance uses the concrete schedule rather than the analytical routing
proxy. The candidate checker reconstructs transport delay for every path in
the global database; paths with no candidate cut nets remain in the objective
with their fixed delay. A lexicographically worse, tied, infeasible, or
unchecked candidate is rolled back. The report checker reconstructs all
successful candidates and replays the acceptance sequence. Raw channel
feedback is not applied as an uncontrolled jump: log-space proximal damping
and deterministic descending backtracking generate positive, reproducible
trial weights between the unweighted and full-feedback objectives.

The throughput-first mode additionally performs an exact checked frame search
for every candidate partition. Its primary objective is the minimum feasible
frame length, which directly determines the nominal pausible-clock emulation
frequency. The next objective is scheduled-path margin against that virtual
clock period. Original RTL clock-domain slack is retained for criticality and
diagnostics, but cannot reject an otherwise closed virtual-runtime candidate
or justify a slower frame. The checker requires both a feasible upper bound
and an explicitly infeasible frame immediately below the selected minimum.

### Phase 5 — TDM scheduling and cycle-accurate transport (scheduling increment implemented)

Implement:

- time-expanded link model;
- OR-Tools CP-SAT provider plus dependency-free heuristic fallback;
- static schedule ROM generation;
- TX/RX, shadow-register, barrier, and virtual-clock-enable RTL;
- multi-partition Verilator simulation harness.

Acceptance:

- no lane/slot collision;
- all multi-hop precedence constraints hold;
- every frame completes before the virtual clock-enable;
- partitioned and unpartitioned designs are cycle-equivalent.

The dependency-free lane/slot scheduler, independent collision/precedence
checker, schedule ROM table, transport manifest, generic link/barrier RTL,
Python event model, and generated SystemVerilog transport simulation are
implemented.

The one-command flow can also treat `frame_slots` as a feasible upper bound
and run checked monotone bisection across the actual Phase 4/5 providers. Each
candidate must pass routing capacity, ratio legalization, concrete schedule,
transport simulation, and pausible-clock barrier validation. Phase 7C then
reports the selected frame's virtual DUT frequency separately from the
original RTL clock-domain path slack.

The joint G6 cycle-equivalence gate is now closed for the mapped PicoRV32
LUT/FF primitive envelope by the Phase 6 split and shadow-endpoint model.

### Phase 6 — Per-FPGA netlist generation and lane/pin planning (board-independent increment implemented)

Implement:

- logical-netlist splitting;
- transport endpoint insertion;
- logical lane assignment;
- virtual IO-region anchoring;
- hardware BSP schema;
- package pin/bank/IOSTANDARD/GT-quad solver;
- XDC generation.

Acceptance:

- every cut endpoint is connected to one generated transport endpoint;
- logical lane maps agree at both ends of each link;
- hardware BSP pin constraints pass an independent electrical-rule checker.

The board-independent increment is implemented with versioned lookahead
position hints, placement-aware pin plans, per-FPGA netlists,
transport-endpoint maps, logical lane maps, virtual anchors, manifests, and
reports. Its root-built C++17 planner constructs minimum-count
ratio-homogeneous TDM groups, refines a placement-region/dispersion objective,
and performs exact group-to-virtual-pin matching. The independent checker
reconstructs group legality and lane/slot occupancy before Phase 6 generates
schedule-specific mux/capture RTL and rechecks exact instance coverage,
endpoint agreement, and mapped cycle behavior.

Package-pin, bank, IOSTANDARD, direction, connector, frequency, and skew
binding is implemented as the Phase 6B source-complete min-cost-flow
increment. It is exercised against an explicit synthetic UltraScale+ BSP and
emits per-FPGA XDC with a synthetic-use warning. Reference-clock, differential
pair/GT specialization, real connector data, and vendor electrical/timing DRC
still require a concrete board-revision-controlled BSP; synthetic validation
is not hardware closure.

### Phase 7 — Integrated open placement and routing

Implement source-complete provider interfaces for:

- a selected open detailed FPGA router;
- an openly reproducible UltraScale+ device-resource/timing model;
- optional Vivado route/DRC/timing comparison;
- optional vendor-assisted bitstream generation.

Acceptance:

- a clean checkout builds the selected placer and router from repository
  source using the root build;
- the default runner invokes only those local build products;
- all per-FPGA designs place and route without a proprietary implementation
  tool;
- the independent checker accepts placement and routing;
- setup/hold and board-interface timing are reported separately;
- reproducible QoR reports include placement, route, TDM, and emulation speed.

The Phase 7A artifact adapters, automatic OpenPARF runner, packed-cluster
handoff, and independent placement checker are implemented. The default
runner resolves only the OpenPARF product compiled by the root build.
External placement files and
installations are comparison-only providers and cannot satisfy the release
gate.

OpenPARF's optional experimental router is not the selected detailed-routing
provider because its upstream build requires proprietary GUROBI. Its source is
retained for provenance, but it is excluded from the open default build.

The VPR detailed-routing result is independently checked against VPR's
exported RR graph. The in-tree C++ checker validates route-node identity,
coordinates and PTC, exact edge/switch connectivity, tree-branch restarts,
cross-net resource capacity, packed-net/sink coverage, and the placement
artifact hash. This is separate from VPR's internal route consistency check.

Physical IO-net preservation, routed DCP validation, timing, and bitstream
generation remain separate gates and are not implied by the placement gate.

Phase 7B emits complete structural primitive Verilog for merged partitions.
Applying placements in Vivado is retained only as optional cross-validation,
not as evidence that the open physical backend is complete.

Phase 7C integrates one lockstep frame controller per transport and formalizes
the current pausible-clock runtime. Existing routed timing measurements are
proprietary cross-validation results until the open router path is connected.

This validates the board-independent logical/runtime contracts for the current
single-virtual-clock envelope. It does not close the open physical
placement/routing gate. Hardware BSP pin binding, source-synchronous board
timing, dedicated clock-buffer binding, bitstream generation, link training,
and a golden hardware workload remain later gates.

Phase 7D seals that result with a versioned release manifest. It rehashes the
pinned RTL and critical artifacts, cross-checks every boundary from partition
counts through routed timing, and records explicit G0-G9 evidence.

### Phase 8 — Open synthesis/packing completion and hardware bring-up

Replace bootstrap mapped DCPs with:

```text
Yosys synth_xilinx -family xcup
  -> UltraScale+ site packer
  -> FPGA Interchange logical/partial physical netlist
```

Add real-board link training, PRBS, deskew, barrier diagnostics, host control,
and golden-workload testing.

Phase 8A is now implemented as the board-independent readiness increment. It
seals a versioned hardware-BSP requirements artifact from the G0-G9 release,
Phase 6 anchors, and virtual BoardDB. It expands physical lane endpoints,
clock/link-channel bindings, per-FPGA bitstream slots, and pending G10 checks,
then independently reconstructs and byte-reproduces the result. It explicitly
reports `awaiting_hardware_bsp` and does not claim G10.

Phase 8B begins after a board is selected: validate a hardware BoardDB/BSP,
bind package pins/banks/IOSTANDARDs and clocking, apply board IO timing, and
generate the first checked bitstreams. Hardware PRBS/training and a golden
workload remain the following G10 increments.

## 6. Provider interfaces

Algorithms are replaceable providers:

```text
SynthesisProvider
PartitionProvider
SystemRouterProvider
TdmSchedulerProvider
PinAssignmentProvider
PlacementProvider
FpgaRouterProvider
BitstreamProvider
```

Provider inputs and outputs are versioned artifacts. Tool-specific log parsing
must not leak into EmuIR or BoardDB.

Open providers are in-tree source components. Yosys/ABC, OpenPARF,
OpenROAD/TritonPart, and RePart source, upstream licenses, exact revision
provenance, and EmuFlow modifications live under `engines/`. The root
CMake build compiles them with the native EmuFlow engines. A compiled
executable is only a local build artifact, never the published implementation.
The default runtime resolver deliberately does not search `PATH`; it selects
the products of this monorepo build.

Presence of source alone is insufficient. A stage is complete only when its
root build target, automatic runner, versioned artifact contract, independent
checker, and clean-checkout end-to-end test all pass. The source manifest
distinguishes `default-in-tree-build` from `source-present-*-pending`; pending
components may not be described as implemented.

Python is the control plane and independent reference/checking layer.
Performance-critical production providers use native C++/CUDA implementations.
The process boundary keeps the GPL-licensed RePart program separate from the
Apache-licensed EmuFlow control plane, while both implementations remain
visible and buildable in the same repository.

## 7. Verification strategy

| Boundary | Required verification |
| --- | --- |
| RTL -> mapped netlist | equivalence and resource report |
| Yosys JSON -> EmuIR | schema, endpoints, resource classifier |
| EmuIR -> partitions | capacity, grouping, cut legality |
| partitions -> TDM RTL | cycle equivalence |
| BoardDB -> route/schedule | independent capacity and collision checker |
| FPGAIF -> OpenPARF -> FPGAIF | name, BEL, pin-map, site-route legality |
| placed -> routed DCP | CheckPhysNetlist, route status, DRC |
| routed DCP -> bitstream | timing summary and vendor bitstream checks |
| bitstream -> hardware | PRBS, link training, barrier, golden workload |

## 8. Current reference configuration

Until a board is selected:

- architecture: pinned VTR flagship heterogeneous 40 nm academic model;
- virtual device: scalable auto layout, initially 64 by 64;
- default virtual platform: academic VTR-class two-FPGA point-to-point;
- per-FPGA utilization limit: 75%;
- logical link: 32 lanes per direction at 250 MHz;
- modeled link latency: two fabric cycles;
- physical mode: out-of-context, no package-pin binding;
- placement provider: root-built in-tree OpenPARF;
- routing provider: root-built in-tree VTR/VPR;
- current placement/routing bridge: the VPR packed-cluster contract,
  OpenPARF clustered placement, checked VPR `.place` emission, and VPR
  detailed routing work for the heterogeneous VTR flagship backend;
- multi-FPGA entry point: `emuflow multi-fpga compile`, with a hash-bound
  report through per-FPGA split and transport generation;
- per-FPGA open physical entry point: `emuflow vpr fpga-open`;
- optional real-device backend: UltraScale+/Vivado.

The device capacities in the virtual platform are planning values. Phase 2 will
replace them with values derived from the selected FPGA Interchange
DeviceResources file.
