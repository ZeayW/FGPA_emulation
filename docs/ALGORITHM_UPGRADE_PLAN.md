# Academic algorithm upgrade plan

## Objective

EmuFlow has validated the board-independent logical contracts through G0-G9.
The root-built OpenPARF placement runner is integrated. The source-complete
physical path is not yet closed because an open UltraScale+
routing/device-model implementation is still required. The next algorithm
campaign improves each optimization stage independently before introducing
cross-stage co-optimization, without weakening those source-completeness
gates.

The existing algorithms remain available as named baselines. A new provider
becomes the default only after it:

1. preserves the existing versioned artifact contract;
2. passes the independent checker for its phase and every affected downstream
   checker;
3. is deterministic for a fixed seed and configuration;
4. is evaluated on a real connected RTL design, ending with the 731,313-cell
   NVDLA CACC acceptance design;
5. reports quality, runtime, peak memory, tool revision, source revision, and
   all configuration values; and
6. improves the phase-specific primary objective without violating resource,
   topology, timing, or transport correctness.

This campaign deliberately freezes upstream and downstream solutions while
one provider is evaluated. Route/TDM, partition/TDM, and
pin/placement co-optimization are a later campaign.

## Scope and order

The first campaign replaces the optimization cores in Phases 3-6:

1. multi-FPGA partitioning;
2. board/die-level system routing;
3. TDM ratio and concrete lane/slot assignment; and
4. logical signal grouping and physical package-pin assignment.

Yosys/ABC remains the Phase 1 synthesis backend and OpenPARF is the Phase 7A
placement provider. Their implementation source is in-tree and must be
launched from the root build. Vivado is retained only for optional
UltraScale+ comparison, DRC, timing, and bitstream sign-off; it is not an
implementation of the open path. Selecting an open device-resource/timing
database and FPGA router is a separate hard gate alongside the Phase 3-6
algorithm campaign.

## Phase 3A — RePart partition-only provider

### Target algorithm

Integrate the open-source RePart implementation from:

- Z. Fu et al., *RePart: Efficient Hypergraph Partitioning with Logic
  Replication Optimization for Multi-FPGA System*, 2026;
- upstream implementation: <https://github.com/Welement-zyf/RePart>.

RePart contributes an FPGA-aware multilevel flow with enhanced coarsening,
initial assignment, move/exchange refinement, topology constraints, and
multi-resource capacity constraints. The upstream implementation is
GPL-3.0-only. Its C++ source, exact revision, license, and EmuFlow changes are
included under `third_party/repart/` and compiled by the root build. The
runtime executes that local build product; no opaque or externally supplied
partitioner binary is part of the provider contract.

Phase 3A first uses RePart's unique-owner partition result. EmuFlow atomic
clusters preserve sequential, hard-macro, and user group constraints. Fixed
assignments and exact EmuFlow capacity limits remain independently enforced
by the existing legality layer and G4 checker.

### Acceptance

- PicoRV32 and NVDLA assignments pass exact coverage, nonempty-partition,
  capacity, fixed/group constraint, and cut-legality checks.
- Two fixed-seed executions produce byte-identical assignment artifacts.
- Report weighted connectivity, cut nets, maximum hop, per-resource balance,
  runtime, and peak RSS against TritonPart.
- RePart must not be promoted merely because it is newer; it must improve the
  declared partition objective or downstream frozen-routing demand.

## Phase 3B — Logic-replication-aware partitioning

RePart's principal contribution includes source-logic replication. Supporting
it faithfully requires a versioned replication artifact rather than silently
discarding `*` output records.

The increment will:

- distinguish an original instance from one or more replicas;
- restrict replication to combinational logic with a formally checked fanin
  cone and clock/reset policy;
- account for every replica in LUT/cell capacity;
- remove only those cut-net demands made local by a legal replica;
- extend Phase 6 netlist generation and cycle-equivalence checking; and
- report communication reduction versus replica area.

This remains a Phase 3 algorithm upgrade: routing and TDM solutions are frozen
and do not feed costs back into partitioning.

The v1 implementation exposes a per-vertex replicability mask to the in-tree
RePart C++ kernel. Only mapped LUT clusters that pass an independent acyclic,
fanin-closed combinational proof may be copied. The resulting
`emuflow.partition-replication/v1` artifact names every physical copy, charges
its resources to the target FPGA, records effective cut-demand deltas, and is
reconstructed independently during validation. Phase 6 materializes those
copies in per-FPGA netlists and compares replica outputs against the original
mapped model over the transport simulation trace.

## Phase 4 — Timing-aware load-balanced die-level router

### Target algorithm

Reimplement the routing component of:

- Y. Chen et al., *Timing-Aware Optimization of Die-Level Routing and TDM
  Assignment for Multi-FPGA Systems*, ASP-DAC 2026.

The Phase 4 provider will consume a fixed Phase 3 assignment and fixed
predicted link-delay table. It will not call the Phase 5 optimizer during this
campaign.

Required components are:

- STA path import with clock-domain and normalized-slack metadata;
- lossless compression of paths sharing an ordered inter-FPGA/die cut
  signature;
- UltraScale+ FPGA/SLR graph modeling with SLL and cable edge classes;
- direction locking for shared physical channel groups;
- criticality- and utilization-aware dynamic edge weights;
- timing-aware net ordering;
- selective critical-path rip-up/reroute; and
- accept/rollback refinement based on worst normalized slack.

The source implementation is now present as the opt-in
`timing-aware-load-balanced-v1` provider. Its optimization core is the
first-party C++17 target `emuflow_tlr_router`, built by the repository root
CMake project. The Python layer only performs versioned STA/BoardDB artifact
adaptation and independent result checking.

The real-STA adapter uses Vivado timing objects rather than parsing formatted
reports. A repository Tcl exporter enumerates nets on each returned path and
joins them through an emitted EmuIR-to-mapped-net identity map. All names are
UTF-8 hex encoded in the interchange TSV before the Python importer creates
the versioned STA artifact.

Implemented algorithm details:

- Eq. (2) multi-clock normalized slack and a lossless compression proof that
  additionally requires equal clock normalization and cut-net sequence;
- fixed link-delay lookup with explicit SLL/cable classification;
- Floyd/Dijkstra-equivalent all-pairs shortest-delay preprocessing for
  majority-flow direction locking;
- ascending normalized-slack and descending predicted-delay demand ordering;
- dynamic delay, criticality, utilization, and historical-overflow weights;
- multicast tree construction by shared predecessor backtrace;
- selective rerouting of nets on the current worst normalized-slack path; and
- lexicographic accept/rollback on worst normalized slack, then maximum
  utilization and bit-hops.

The fixture gate is complete: a multi-clock capacity-constrained diamond sends
the critical net over the 2 ns path where the baseline assigns it the 10 ns
path; ordered-signature compression, shared-direction locking, independent
delay/slack recomputation, and corruption rejection all pass. The NVDLA
large-design scale, determinism, and frozen Phase 5/6 compatibility gates also
pass with a deterministic structural timing workload. The real-STA gate also
passes on the same 731,313-cell NVDLA design: Vivado timing objects are joined
to stable cut-net identities, repeated route artifacts are byte-identical,
and the frozen-baseline worst normalized slack improves without capacity or
downstream Phase 5/6 violations. The academic provider is therefore selected
automatically whenever `--timing-paths` is present; the negotiated provider
remains the no-STA feasibility fallback.

The existing negotiated-congestion router remains
`negotiated-shortest-path-tree-v1`.

### Acceptance

- Preserve G5 reachability, tree, direction, latency, and capacity legality.
- With the partition and predicted ratios frozen, improve worst normalized
  slack; use maximum utilization, total bit-hops, maximum hop, runtime, and
  memory as secondary metrics.
- Validate first on a multi-path/multi-clock fixture, then PicoRV32, then the
  NVDLA acceptance design.

## Phase 5 — Lagrangian TDM ratio optimization plus exact scheduling

### Target algorithm

Implement the fixed-route TDM portion of the ASP-DAC 2026 method, informed by
the scalable continuous/discrete formulations in:

- *An Analytical Approach for Time-Division Multiplexing Optimization in
  Multi-FPGA-based Systems*, SLIP 2019; and
- *Lagrangian Relaxation-Based Time-Division Multiplexing Optimization for
  Multi-FPGA Systems*, TODAES 2020.

The new provider has two levels:

1. optimize continuous ratios on the fixed-route timing graph, then legalize
   them to the platform's discrete ratio set with direction-group capacity
   constraints and timing-aware post-refinement; and
2. expand the optimized channel groups and ratios into a concrete
   lane/slot schedule.

The current successor-set earliest-slot scheduler and its independent
collision, precedence, round-barrier, and value-transport checkers remain the
exact schedule realization and validation layer.

The implementation is now available as
`lagrangian-kkt-timing-aware-v1`. Its optimization kernel is the first-party
C++17 target `emuflow_tdm_ratio_optimizer`, compiled by the root CMake
project. It solves the bounded continuous channel-capacity problem with path
dual multipliers and per-domain KKT updates, legalizes to ratio 1 or
quantized multiples of 8, forms direction-homogeneous lane groups, and applies
timing-aware ratio/lane swaps.

EmuFlow's two transport rounds add a global barrier constraint that is not
explicit in the cited fixed-route formulations. A deterministic legalization
layer therefore searches a common round boundary, promotes only the ratio
groups needed to remove lane fragmentation, and rebalances signals within
homogeneous groups before exact list scheduling. The independent checker
reconstructs continuous capacity, discrete group legality, barrier capacity,
path delay/slack, lane/slot occupancy, multi-hop precedence, and value
transport without trusting optimizer summary fields.

Timing-annotated Phase 4 routes select this provider automatically. Routes
without STA metadata retain
`deterministic-round-barrier-earliest-slot-v2` as the feasibility baseline.

### Acceptance

- Preserve G6 collision, precedence, round-barrier, and transport simulation.
- Improve worst normalized slack or the minimum legal frame length on frozen
  Phase 4 routes.
- Report ratio distribution, maximum ratio, frame slots, completion slot,
  nominal virtual DUT frequency, runtime, and memory.
- The fixed 4,096-slot NVDLA baseline must be compared against the smallest
  independently legal frame found by the new provider.

## Phase 6A — Timing-aware logical signal grouping

With the Phase 5 route and ratio solution frozen, group compatible signals
into logical channels using:

- direction and route compatibility;
- clock domain;
- timing criticality;
- register-output/register-input transport round;
- serialization ratio; and
- deterministic lane occupancy.

The result must keep the exact endpoint/lane-map contract and mapped
cycle-equivalence checks. It must improve logical-channel count, critical
channel pressure, or completion slot without changing partition or route
topology.

## Phase 6B — Physical package-pin assignment

Phase 6B is implemented as a constraint solver over a versioned hardware
BoardDB/BSP. Hard constraints include:

- package pin, connector, and peer-pin connectivity;
- I/O bank voltage and IOSTANDARD;
- direction;
- differential pairs;
- clock-capable pins and forwarded-clock requirements;
- per-bank capacity; and
- reserved and unavailable pins.

The objective combines timing criticality, SLR-to-I/O distance, connector and
bank congestion, skew, and deterministic tie breaking. CP-SAT/ILP and
min-cost-flow formulations will be evaluated against exact small fixtures.

Until a board is selected, the solver is validated on a fully specified
synthetic UltraScale+ BSP and is not described as hardware closure.

## Promotion and experiment protocol

Each provider increment follows the same sequence:

1. unit tests and adversarial legality fixtures;
2. deterministic repeated run;
3. small real RTL smoke test;
4. medium connected RTL test;
5. 731,313-cell NVDLA acceptance run on a configured large-design worker;
6. frozen-baseline QoR table;
7. independent downstream validation; and
8. commit and push only the reusable implementation, schemas, tests, and
   project-level documentation.

Exact commands, machine configuration, artifact hashes, raw measurements, and
QoR tables are maintained as local experiment records and are not committed.
The default provider changes only at step 8. Cross-stage feedback remains
disabled until Phases 3-6 all have promoted academic providers.
