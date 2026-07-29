# Academic algorithm upgrade plan

## Objective

EmuFlow has a board-independent, feasibility-complete path through G0-G9.
The next development campaign improves the optimization quality of each
stage independently before introducing any cross-stage co-optimization.

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

Yosys/ABC remains the Phase 1 synthesis backend, OpenPARF remains the Phase 7A
placement backend, and Vivado remains the UltraScale+ implementation router
while no complete open UltraScale+ routing-resource database and bitstream
flow is available. These backends can receive separate algorithm campaigns
after Phases 3-6.

## Phase 3A — RePart partition-only provider

### Target algorithm

Integrate the open-source RePart implementation from:

- Z. Fu et al., *RePart: Efficient Hypergraph Partitioning with Logic
  Replication Optimization for Multi-FPGA System*, 2026;
- upstream implementation: <https://github.com/Welement-zyf/RePart>.

RePart contributes an FPGA-aware multilevel flow with enhanced coarsening,
initial assignment, move/exchange refinement, topology constraints, and
multi-resource capacity constraints. The upstream implementation is GPL-3.0;
EmuFlow will invoke a separately built external binary and record its source
revision and license rather than copy its implementation into this repository.

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
5. 731,313-cell NVDLA acceptance run on `proj169-2`;
6. frozen-baseline QoR table;
7. independent downstream validation; and
8. commit, push, and a phase validation record containing exact commands and
   artifact hashes.

The default provider changes only at step 8. Cross-stage feedback remains
disabled until Phases 3-6 all have promoted academic providers.
