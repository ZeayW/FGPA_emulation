# Academic algorithm research and implementation roadmap

## 1. Objective

EmuFlow will pause further heuristic-provider development until each major
optimization stage has a literature-backed technical specification. The
target is not to attach paper names to familiar primitives such as Dijkstra,
FM, Hungarian matching, or min-cost flow. A paper-backed provider must
reproduce the paper's:

- problem definition and assumptions;
- decision variables and hard constraints;
- objective function and normalization;
- principal optimization procedure;
- continuous-to-discrete legalization;
- post-refinement procedure; and
- evaluation metrics and ablations.

The implementation may extend a paper to support EmuFlow-specific semantics,
such as multicast transport, two communication rounds, a global frame barrier,
or heterogeneous boards. Every extension must be isolated from the faithful
paper model and independently checked.

This document is a technical roadmap, not an experiment log. Machine
configuration, commands, raw results, artifact hashes, and benchmark tables
remain local.

## 2. Literature review protocol

Implementation of a stage starts only after its literature gate is complete.
For each stage, the review must contain:

1. at least one classical formulation;
2. at least three modern directly relevant methods where the literature
   exists;
3. the best available open-source implementation;
4. a comparison of variables, constraints, objectives, complexity, and
   scalability;
5. a list of assumptions that do not hold in EmuFlow;
6. a selected primary algorithm and at least one independent baseline; and
7. a mathematical test oracle for small instances.

Each selected paper is classified as:

- **direct integration**: upstream source is imported and built in-tree;
- **faithful reproduction**: EmuFlow implements the published formulation;
- **paper-informed extension**: the paper is a starting point, but the model
  is materially changed; or
- **background only**: the paper informs design choices but is not claimed as
  an implemented algorithm.

No provider is described as a reproduction until its equation-level
specification and small-instance oracle agree.

## 3. Common implementation standard

Every optimization stage follows the same implementation sequence:

1. write a provider-neutral mathematical model;
2. implement an exact small-instance oracle using ILP, CP-SAT, dynamic
   programming, or exhaustive enumeration as appropriate;
3. reproduce the selected paper algorithm without EmuFlow-specific
   enhancements;
4. compare the reproduction against the oracle on small adversarial cases;
5. implement a scalable C++ provider;
6. add EmuFlow extensions behind separate options;
7. reload the artifact with an independent checker;
8. validate on small, medium, 100K-cell, and NVDLA-scale connected RTL;
9. compare against the frozen baseline and paper ablations; and
10. promote the provider only if it improves the declared primary objective
    without weakening correctness.

Python remains the artifact, orchestration, reference-model, and checker
layer. Performance-critical optimization is implemented in C++ or CUDA.

### Stage selection summary

| Stage | Faithful primary route | Independent alternatives/oracle |
| --- | --- | --- |
| Architecture/timing | VTR XML + provider-neutral TimingDB + OpenSTA | FPGA Interchange/ECP5 adapters; Vivado comparison |
| Synthesis/mapping | Yosys/ABC9 | Mapping Fusion experiment; equivalence oracle |
| Hypergraph model | ASP-DAC 2025 adaptive modeling | complete flattening |
| Partitioning | MFSPart-Ensemble + RePart replication | TritonPart, SHyPar, exact ILP |
| System routing | DAC 2025 die-level router + ASP-DAC 2026 timing refinement | candidate-tree MIP |
| TDM | TODAES 2020 LR + exact schedule realization | SLIP 2019, CP-SAT |
| Pin planning | Chimew | exact matching/min-cost-flow oracle |
| Placement | OpenPARF + LEAPS/TD-Placer mechanisms | Vivado comparison |

## 4. Stage 0 - Architecture, timing, and board models

### Why this stage comes first

Timing-driven partitioning, routing, TDM, pin planning, and placement cannot
be academically meaningful when they optimize unrelated delay proxies. The
current Vivado timing-path adapter is useful for comparison, but an open,
provider-neutral timing and architecture model is required before timing
algorithms are promoted.

### Literature and source foundations

- VTR/VPR architecture XML and timing infrastructure;
- OpenSTA and OpenROAD timing infrastructure;
- FPGA Interchange DeviceResources as an optional real-device adapter;
- *Challenges in Large FPGA-Based Logic Emulation Systems*, ISPD 2018;
- UltraScale+/multi-die placement literature describing SLR, SLL, clock
  region, and heterogeneous-resource constraints.

### Technical route

1. Import the public VTR flagship architecture into provider-neutral
   ArchitectureDB and Architecture TimingDB artifacts. This increment is
   implemented for layout, relaxed capacity, primitive/block arcs, switches,
   segments, and directs.
2. Preserve mutually exclusive VTR modes and equivalent sites in an exact
   packing contract; connect generic Yosys/ABC mapping to those modes.
3. Translate Architecture TimingDB cell and interconnect arcs into OpenSTA,
   retain stable EmuIR path identities, and project maximum path criticality
   into power-law TritonPart hyperedge weights. This pre-placement increment
   is implemented for LUT, FF, multiplier, and synchronous RAM mappings.
   Candidate selection independently recomputes the weighted cut objective
   across all requested seeds and an automatic unweighted baseline, preventing
   timing mode from silently regressing below its same-seed baseline.
4. Build the VTR routing-resource graph and integrate source-built VPR detailed
   routing after OpenPARF placement.
5. Extend BoardDB from link-level capacity to direction groups, cable/SLL
   delay, lane clock, bank location, and physical channel constraints.
6. Produce one partition-independent timing-path database with stable EmuIR
   net identities.
7. Add ECP5 and FPGA Interchange/UltraScale+ adapters behind the same
   contracts. Commercial results may compare or calibrate an optional backend,
   but never define the default open model.
8. Version every model and record its confidence/qualification level.

### Acceptance gate

- OpenSTA path identity and clock domains agree with the mapped netlist.
- Timing paths are independent of a candidate partition.
- The selected architecture's region, resource, and routing constraints are
  present in the open model.
- Optional real-device comparison error is reported by path class; it is not
  silently absorbed into the academic model.

## 5. Stage 1 - Logic synthesis and FPGA technology mapping

### Reviewed directions

- Yosys and ABC;
- ABC9 timing-aware FPGA mapping;
- *Narrowing the Synthesis Gap: Academic FPGA Tools vs. Industry*, DATE 2023;
- *Mapping Fusion: Improving FPGA Technology Mapping with ASIC Mapper*,
  2025;
- classical cut-based LUT mapping and FlowMap;
- Lakeroad sketch-guided mapping for heterogeneous hard primitives.

### Decision

Do not reproduce a general logic synthesizer. Continue direct integration of
Yosys/ABC, but evaluate ABC9 and Mapping Fusion as timing/area mapping
experiments before choosing the default mapping path. Hard-block mapping
research is optional and follows stable LUT/FF mapping.

### Technical route

1. Freeze the current `synth_xilinx` result as the compatibility baseline.
2. Add an ABC9 provider with architecture and multi-clock delay information.
3. Preserve stable sequential-boundary identity through synthesis.
4. Add formal/sequential equivalence between provider outputs.
5. Compare LUT count, depth, critical delay, hard-block inference, and
   downstream partition cut quality.
6. Reproduce the Mapping Fusion experiment as an optional provider:
   interleave ASIC-cell mapping and LUT mapping, first with a deterministic
   search policy and only then with its learned design-selection policy.
7. Investigate Lakeroad only for DSP/BRAM/complex primitive coverage that
   Yosys mapping demonstrably misses.

### Acceptance gate

- Formal equivalence passes.
- No unsupported primitive enters EmuIR.
- ABC9 improves timing or area on real RTL without degrading partition
  legality.

## 6. Stage 2 - Hypergraph construction and safe clustering

### Core literature

- Karypis et al., multilevel hypergraph partitioning, DAC 1997/1999;
- *Efficient Hypergraph Modeling of VLSI Circuits for the MFS-Based
  Emulation and Simulation Acceleration*, ASP-DAC 2025;
- timing-driven circuit-partitioning work using path-based objectives;
- TritonPart and RePart input models.

### Current gap

The current union-find clustering is a correctness-preserving flat-netlist
transformation. It does not provide adaptive hierarchical flattening, clock
modeling, path-aware net construction, or a controlled tradeoff between
hypergraph size and lost partition freedom.

### Technical route

1. Separate semantic atomicity from scalability clustering.
   Semantic clusters preserve state, combinational-cut policy, macros, and
   user groups; scalability clusters remain reversible.
2. Reproduce the ASP-DAC 2025 adaptive hierarchical flattening and parallel
   clock-modeling methods for large hierarchical RTL.
3. Represent:
   - multi-dimensional FPGA resources;
   - ordered timing paths;
   - clock domains;
   - replication eligibility;
   - fixed and grouped cells;
   - topology candidate sets; and
   - transportable versus forbidden cuts.
4. Preserve a lossless map from every hypergraph vertex and hyperedge back to
   EmuIR.

### Acceptance gate

- Hypergraph construction scales to multi-million-cell hierarchical designs.
- Every semantic cluster is preserved.
- Flattening choice is reproducible and resource aware.
- Partition QoR is compared with complete flattening at equal constraints.

## 7. Stage 3 - Multi-FPGA partitioning

### Core literature

- TritonPart, *An Open-Source Constraints-Driven General Partitioning
  Multi-Tool for VLSI Physical Design*, ICCAD 2023;
- RePart, *Efficient Hypergraph Partitioning with Logic Replication
  Optimization for Multi-FPGA System*, 2026;
- MFSPart and MFSPart-Ensemble, generalized multi-FPGA partitioning with
  decoupled coarsening/propagation and cut-overlay ensembling, TCAD 2026;
- TopoPart, topology-driven partitioning, ICCAD 2021;
- *Timing Driven Partition for Multi-FPGA Systems with TDM Awareness*,
  ISPD 2020;
- MaPart, multi-FPGA-system-aware partitioning, TCAD 2024;
- EasyPart, FPGA-emulation hypergraph partitioning, ICCAD 2024;
- SpecPart/K-SpecPart spectral and cut-overlay improvement;
- SPARK partitioning/routing, GLSVLSI 2023;
- BlasPart deterministic parallel partitioning, DAC 2025;
- SHyPar, effective-resistance spectral coarsening plus flow-based community
  refinement, TCAD 2025/2026;
- HySpecPro, GPU spectral-embedding projection optimization, 2026 preprint;
- preference-guided parameter tuning, ASP-DAC 2026.

### Baselines

- in-tree TritonPart multilevel hypergraph mode;
- in-tree RePart unique-owner mode;
- exact ILP/CP-SAT oracle for small hypergraphs.

### Selected primary route

Reproduce MFSPart's generalized multilevel framework as the primary
multi-FPGA engine, and integrate RePart's logic-replication mechanism as a
separate refinement operator. This replaces the earlier assumption that
RePart alone should be the primary algorithm:

1. **Decoupled coarsening, propagation, and initialization**
   - MFSPart affinity coarsening before candidate-FPGA propagation;
   - margin coarsening around fixed nodes;
   - driver-sink decomposition while retaining the original hyperedge view;
   - delayed candidate-FPGA propagation from the coarsest hypergraph;
   - two-phase probabilistic feasible assignment and deterministic violation
     repair;
   - fixed/group/multi-resource constraints.
2. **Multi-objective refinement**
   - MFSPart driver-sink cut, connectivity, mean-hop, and low-hop constraints;
   - distance-aware FM gains and restricted-neighborhood moves;
   - maximum pairwise cut and predicted TDM pressure;
   - path-based timing penalty, not only per-net criticality;
   - direct K-way FM, pairwise FM, and hyperedge refinement;
   - MFSPart-Ensemble cut-overlay recombination across selected uncoarsening
     levels.
3. **Logic replication**
   - RePart replication restricted by EmuFlow's semantic proof;
   - exact replica capacity charge;
   - timing/cut benefit evaluated after materialization.
4. **Quality escape**
   - compare SHyPar spectral coarsening, SpecPart/K-SpecPart, and MFSPart
     cut-overlay refinement under identical multi-FPGA constraints;
   - keep HySpecPro experimental until it supports nonuniform node weights
     and multi-resource legality; use it initially only to generate candidate
     solutions for constrained repair;
   - deterministic parallelism investigated after the serial reference is
     correct.

TritonPart's native OpenSTA timing-aware netlist mode must be evaluated
separately from EmuFlow's weighted-hypergraph adapter. They are not treated as
the same algorithm.

### Primary objective

Lexicographic feasibility first:

1. semantic cut legality;
2. multi-resource capacity;
3. topology and maximum-hop feasibility;
4. worst predicted path slack;
5. maximum pairwise cut/TDM pressure;
6. weighted connectivity and replica area.

### Acceptance gate

- Exact oracle agreement on small cases.
- Zero semantic, capacity, fixed/group, and topology violations.
- Paper-level ablations for coarsening, initialization, refinement, and
  replication.
- Better frozen downstream routing/TDM result, not only lower raw cutsize.

## 8. Stage 4 - System-level and die-level routing

### Core literature

- 2019 ICCAD CAD Contest system-level FPGA routing problem;
- *Time-Division Multiplexing Based System-Level FPGA Routing for Logic
  Verification*, DAC 2020;
- *Routing Topology and TDM Co-Optimization for Multi-FPGA Systems*,
  DAC 2020;
- *Multi-FPGA Co-Optimization: Hybrid Routing and TDM Assignment*,
  ASP-DAC 2021;
- ALIFRouter, DATE 2021;
- SPARK, GLSVLSI 2023;
- MaPart's layered-graph router, TCAD 2024;
- [*Synergistic Die-Level Router for Multi-FPGA System with Time-Division
  Multiplexing Optimization*](https://yibolin.com/publications/papers/ROUTE_DAC2025_Wang.pdf),
  DAC 2025;
- [*Timing-Aware Optimization of Die-Level Routing and TDM Assignment for
  Multi-FPGA Systems*](https://numbda.cs.tsinghua.edu.cn/papers/aspdac262.pdf),
  ASP-DAC 2026;
- Steiner-tree and shallow-light-tree methods including KMB/Mehlhorn and
  SALT.

### Implemented milestone

The C++ provider now constructs two independently checkable topology
candidates: the original source-rooted shortest-path tree and a DAC
2025-informed delay-demand-balanced connection tree. The latter connects
high-cost sinks incrementally through legal Steiner attachment points and
uses distinct SLL and cable/TDM congestion costs. The provider selects the
better complete solution with the ASP-DAC 2026 worst-normalized-slack
objective before selective critical-path rip-up/reroute. A separate Python
oracle exhaustively enumerates direction-feasible directed arborescences and
global tree combinations on small graphs.

For reproducible ablation, `timing-aware-load-balanced-v1` executes only the
shortest-path/TLR candidate, while
`timing-aware-route-tdm-cooptimized-v1` enables both candidates and the
lexicographic selector. They no longer alias the same native execution mode.

This is classified as a **paper-informed extension**, not yet a faithful DAC
2025 reproduction: the public EmuFlow BoardDB model is more general than the
contest topology, and paper benchmark/result reproduction is still pending.
The remaining topology gap is candidate-tree column generation or LNS over a
larger KMB/Mehlhorn and shallow-light candidate pool.

### Selected primary route

1. **Candidate topology generation**
   - shortest-path tree baseline;
   - KMB/Mehlhorn Steiner approximation;
   - shallow-light-tree candidates for bounded source-to-sink delay;
   - DAC 2025 delay-demand-balanced negotiated path search with distinct SLL
     and TDM edge timing models;
   - congestion-perturbed and direction-feasible alternatives;
   - multicast sharing retained explicitly.
2. **Global tree selection**
   - restricted master formulation over candidate trees;
   - capacity, direction group, SLL, cable, and hop constraints;
   - Lagrangian/column-generation-style pricing or deterministic
     large-neighborhood refinement;
   - exact MIP oracle for small instances.
3. **Timing-aware refinement**
   - DAC 2025 connection ordering plus multithreaded Lagrangian TDM-ratio
     estimation and margin-aware legalization as a reproducible baseline;
   - ASP-DAC 2026 lossless timing-path compression;
   - majority-flow direction initialization;
   - adaptive timing/load edge weights;
   - selective rerouting of trees on the worst normalized-slack paths;
   - accept/rollback using independently reconstructed path slack.
4. **Routing/TDM interface**
   - pass multiple route candidates or marginal congestion prices to Stage 5;
   - do not collapse the problem to a single scalar predicted ratio too early.

### Primary objective

Maximize worst normalized path slack subject to exact reachability, direction,
hop, and channel-capacity legality. Maximum channel utilization, maximum TDM
pressure, total bit-hops, and runtime are secondary objectives.

### Acceptance gate

- Exact tree-selection oracle agreement on small graphs.
- Reproduction of DAC 2020/ASP-DAC 2021/ASP-DAC 2026 ablations.
- Multicast, half-duplex, unavailable-link, SLL/cable, and asymmetric
  direction tests.
- Improvement survives exact Stage 5 scheduling.

The small multicast exact-oracle gate is implemented. Contest-scale
reproduction and the larger candidate-pool gate remain open.

## 9. Stage 5 - TDM ratio, grouping, lane, and slot assignment

### Core literature

- virtual wires and phase assignment;
- ICCAD 2018 simultaneous partitioning and signal grouping;
- *An Analytical Approach for Time-Division Multiplexing Optimization in
  Multi-FPGA-Based Systems*, SLIP 2019;
- [*Lagrangian Relaxation-Based Time-Division Multiplexing Optimization for
  Multi-FPGA Systems*](https://cwpui.com/doc/j4.pdf), TODAES 2020;
- DAC 2020 routing/TDM co-optimization;
- ASP-DAC 2021 hybrid routing/TDM;
- *An Integrated Circuit Partitioning and TDM Assignment Optimization
  Framework*, ASP-DAC 2023;
- ASP-DAC 2026 timing-graph-based TDM assignment.

### Important separation

The literature usually optimizes TDM ratios or signal groups. EmuFlow also
requires a concrete, collision-free, multi-hop lane/slot schedule. Ratio
optimization and schedule realization are separate optimization problems and
must have separate oracles.

### Implemented milestone

The in-tree C++ ratio optimizer solves a path-form Lagrangian/KKT continuous
relaxation and performs critical-path swap refinement. Its discrete stage now
implements the TODAES 2020 minimum-feasible maximum-displacement search and
exact total-displacement dynamic program. Interval-cost precomputation reduces
the exact segmentation to `O(lanes * signals^2)` and raises the exact domain
limit to 2,048 signals. Still larger domains retain the deterministic
minimum-wire construction to bound runtime.
Independent exhaustive Python oracles verify the exact displacement objective
on small domains and the realized timing optimum of compact single-round
lane/slot schedules.

Exact ratio displacement is not assumed to imply a better concrete schedule.
The Phase 5 driver evaluates both the exact-DP and scalable minimum-wire
legalizations, realizes and checks both lane/slot schedules, and selects
lexicographically by independently reconstructed worst, p01, and median
normalized slack, followed by completion slot and analytical slack. This
guard prevents an improvement in the ratio surrogate from silently degrading
the realized schedule.

The concrete lane/slot realization remains the ratio-aware deterministic list
scheduler with independent collision, precedence, round-barrier, and value
simulation checks. Therefore the ratio/legalization upgrade is implemented,
while multi-round time-expanded CP-SAT scheduling and scalable schedule LNS
are still open; the stage is not yet described as a complete faithful
TODAES/ASP-DAC 2026 reproduction.

### Selected primary route

1. **Continuous ratio optimization**
   - faithfully reproduce the TODAES 2020 Lagrangian-relaxation solver;
   - independently reproduce the SLIP 2019 nonlinear-CG formulation as a
     comparison provider;
   - use path-level timing and per-direction capacity constraints.
2. **Discrete ratio legalization**
   - reproduce binary-search/DP discretization and displacement objective;
   - support legal ratio sets, clock groups, direction groups, and channel
     capacity;
   - reproduce swap/post-refinement on critical paths.
3. **Exact schedule oracle**
   - formulate lane/slot assignment as CP-SAT on a time-expanded graph;
   - include lane collision, precedence, latency, multicast, two transport
     rounds, and the global barrier;
   - use it as an oracle for small and medium cases.
4. **Scalable schedule construction**
   - decompose by capacity domain and timing component;
   - use list scheduling only for initialization;
   - apply large-neighborhood repair guided by the CP-SAT model;
   - allow controlled frame-length search.
5. **Timing reconstruction**
   - compute realized wait from concrete slots for every global STA path;
   - use analytical ratio slack only as a bound, never as final QoR.

### Primary objective

Maximize realized worst normalized slack. Secondary objectives are minimum
legal frame length, completion slot, maximum ratio, and lane usage.

### Acceptance gate

- Continuous and discrete paper formulations match their reference equations.
- Exact-oracle agreement on small schedules.
- Zero collision, precedence, round-barrier, or transport-value mismatch.
- Academic provider improves realized schedule timing, not only continuous
  ratio estimates.

## 10. Stage 6 - TDM signal grouping and package-pin assignment

### Core literature

- classical multi-FPGA pin assignment;
- *Pin Assignment Optimization for Multi-2.5D FPGA-Based Systems*,
  ISPD 2018;
- the 2021 TCAD extension for time-multiplexed I/Os;
- *TDM Signal Grouping and Package Pin Assignment for 2.5D Multi-FPGA
  Systems with Lookahead Placement* (Chimew), FPGA 2026.

### Current gap

The current balanced coloring, pairwise swaps, and Hungarian assignment are a
baseline. They do not faithfully reproduce Chimew's die-crossing encoding,
RUDY congestion gate, two-stage bank/pin assignment, or published edge-cost
model.

### Selected primary route: faithful Chimew reproduction

1. Construct the FPGA-level lookahead netlist with die fences, cross-FPGA
   endpoints, cross-die nets, and bypass signals.
2. Run accelerated OpenPARF global placement and compute RUDY congestion.
3. Reject or feed back partitions whose lookahead congestion exceeds a
   qualified threshold.
4. Encode each signal's source/sink SLL-crossing signature.
5. Reproduce encoding-based greedy grouping:
   - same direction and ratio;
   - descending crossing count;
   - nearest compatible encoding;
   - exact group capacity.
6. Reproduce placement-based grouping refinement without increasing the
   crossing objective.
7. Reproduce two-stage package assignment:
   - bank-pair assignment;
   - channel/package-pin assignment;
   - min-cost-flow or equivalent exact weighted bipartite matching;
   - source-fanout and sink-fanin distance costs from lookahead placement.
8. Extend the faithful model separately with:
   - slot conflict;
   - differential pairs;
   - clock-capable pins;
   - forwarded clocks;
   - bank voltage/IOSTANDARD;
   - skew and frequency;
   - GT channels.

### Acceptance gate

- Exact matching oracle agreement.
- Published grouping and edge-cost equations are reconstructed independently.
- Zero electrical, direction, bank, connector, and pin-collision violations.
- Lookahead congestion/SLL metrics correlate with final Vivado results.
- Synthetic BSP results are labelled algorithm validation; hardware closure
  requires a real revision-controlled BSP.

## 11. Stage 7 - OpenPARF placement and Vivado handoff

### Core literature

- OpenPARF, ASICON 2023;
- multi-electrostatic heterogeneous FPGA placement, DAC 2022;
- LEAPS multi-die/SLL-aware placement, TCAS-I 2023;
- OpenPARF 3.0 macro placement, ISEDA 2024;
- AMF-Placer 2.0 timing-driven mixed-size placement;
- DREAMPlaceFPGA-MP macro/fence placement;
- TD-Placer critical-path-aware timing-driven global placement, 2025;
- Chimew's accelerated lookahead placement, FPGA 2026.

### Two distinct placement products

1. **Lookahead placement** serves partition, routing, TDM, and pin planning.
   It must be fast and predict congestion, SLL crossing, and endpoint
   location.
2. **Handoff placement** provides a legal and useful starting point for
   Vivado. It must model enough packing and device constraints that Vivado
   does not discard the result.

They must not be evaluated by the same acceptance criteria.

### Selected technical route

1. Keep direct in-tree OpenPARF integration as the global-placement engine.
2. Enable and validate the complete heterogeneous-resource model.
3. Add LEAPS-style SLR/SLL objectives and continuous multi-die optimization.
4. Add timing weights using OpenSTA paths; evaluate AMF-Placer/TD-Placer
   timing models before selecting one.
5. Add RUDY or a stronger routability estimator and correlate it with Vivado
   congestion.
6. Support macro cascades, fences, clock regions, and fixed I/O resources.
7. Replace the bucket greedy legalizer with a pack/control-set-aware
   legalization model for handoff:
   - LUT/FF compatibility;
   - FF control sets;
   - carry/macro chains;
   - site capacity and pin sharing;
   - SLR and clock-region legality.
8. Treat Vivado constraints as a graduated handoff:
   soft regions, movable locations, and sparse hard anchors. Do not assume
   that more fixed LOCs imply better placement.

### Acceptance gate

Lookahead:

- congestion and SLL rank correlation against Vivado;
- endpoint-location stability;
- runtime suitable for iterative use.

Handoff:

- complete compatible placement artifact;
- bounded Vivado displacement and repair count;
- no regression in placement/routing success;
- measured Vivado runtime, congestion, WNS/TNS, SLL usage, and anchor
  retention against a no-OpenPARF baseline.

Vivado remains the final placement/routing/sign-off backend for the current
project scope.

## 12. Cross-stage optimization campaign

Cross-stage work begins only after the corresponding single-stage providers
pass their literature and acceptance gates.

### Loop A - Partitioning, routing, and TDM

Foundations:

- ICCAD 2018 simultaneous partitioning/grouping;
- ISPD 2020 TDM-aware timing partitioning;
- ASP-DAC 2023 integrated partitioning/TDM;
- MaPart, EasyPart, and SPARK;
- DAC 2020, ASP-DAC 2021, and ASP-DAC 2026 routing/TDM methods.

Technical route:

1. freeze one partition-independent STA database;
2. solve exact routing and scheduling for the incumbent;
3. derive path-, channel-, hop-, and ratio-specific marginal costs;
4. update partition objectives through a trust-region/proximal step;
5. rerun partitioning, routing, and exact scheduling;
6. accept only an independently reconstructed all-path improvement;
7. roll back infeasible, tied, or regressing candidates.

### Loop B - Pin planning and placement

Foundation: Chimew.

Technical route:

1. generate lookahead placement;
2. group signals and assign banks/pins;
3. insert transport logic and rerun lookahead placement;
4. recompute RUDY, SLL crossing, and endpoint distance;
5. refine grouping/pins or request repartitioning;
6. stop on objective convergence or a strict iteration budget.

### Loop C - Full flow

Only after Loops A and B are independently stable:

1. partition;
2. route and assign TDM;
3. construct transport logic;
4. run lookahead placement;
5. assign pins;
6. evaluate congestion and timing;
7. update the highest-impact upstream decision;
8. accept/rollback with a multi-stage trust region.

A monolithic all-stage optimizer is explicitly deferred until these nested
loops establish reliable objective sensitivities.

## 13. Implementation order

The planned order is:

1. **R0 - Open architecture and timing foundation**
2. **R1 - Hypergraph construction and faithful advanced partitioning**
3. **R2 - Candidate-tree and timing-aware system routing**
4. **R3 - Faithful TDM ratio optimization and exact scheduling**
5. **R4 - Faithful Chimew grouping and package-pin assignment**
6. **R5 - SLR/timing/routability-aware OpenPARF placement**
7. **R6 - Partition/routing/TDM closed loop**
8. **R7 - Pin/placement closed loop**
9. **R8 - Full pre-Vivado outer loop**

Each increment is merged only after its exact oracle, independent checker,
real RTL validation, and frozen-baseline comparison pass.

## 14. Immediate next action

R0 now has a source-built C++ VTR XML importer, provider-neutral
ArchitectureDB/Architecture TimingDB contracts, and source-built VPR exact
packing, baseline placement, routing-resource-graph construction, detailed
routing, and analysis. The pinned public flagship model supplies heterogeneous
layout and timing data without commercial device files. The ArchitectureDB
view remains a relaxed planning view across mutually exclusive modes; exact
packing legality comes from VPR consuming the original XML.

R0 also has source-built standalone OpenSTA and optional FPGA Interchange
importers. The older analytical UltraScale+ model and mixed-license
RapidWright-produced region data remain optional compatibility inputs, not the
definition of the open research flow.

The packed-netlist contract is now implemented by a C++ VPR `.net` importer
plus an independent validator. It preserves selected modes, the complete used
pb hierarchy, atom membership, cross-cluster nets, and source hashes.

Packed clusters are now exported with exact VTR site capacities to the
source-built OpenPARF analytical placer. Architecture-defined single-site
resources use OpenPARF's min-cost-flow legalizer; the independently checked
result is emitted as VPR `.place` and accepted by source-built VPR routing.
The routed result is then checked independently of VPR: a streaming C++17
checker matches every route node to the RR graph, verifies exact
edge/switch connectivity and route-tree branch restarts, reconstructs
per-resource occupancy/capacity, and binds net/sink coverage to the packed
contract and placement hash.

The pinned flagship mapping profile now preserves Yosys multiplier and
synchronous RAM inference through eBLIF. Fixed-width mapping cells implement
the architecture's legal multiplier modes, while `memory_libmap` selects its
legal 32-Kibit single/dual-port modes. VPR performs the final mode-aware
packing. ArchitectureDB dimensions are taken from VPR's actual auto-layout
placement rather than a design-specific constant.

The same per-FPGA path is exposed as one checked `vpr fpga-open` transaction. Its
aggregate contract cross-binds the synthesized eBLIF, VPR packed netlist,
ArchitectureDB dimensions, OpenPARF placement, routed RR graph, and independent
route-check report. The explicit commands remain available for algorithm
development, but are no longer the only way to assemble the open backend.

The remaining R0/R1 work is:

- generalize the implemented flagship multiplier/RAM mapper into
  architecture-specific mapping profiles and add remaining hard-block types;
- translate Architecture TimingDB into OpenSTA cell/interconnect models.

Until R0 is complete, existing timing-aware providers remain research
prototypes and are not promoted as definitive paper reproductions.

## 15. Recent online literature additions

The local paper collection remains the main review corpus. The following
newer primary sources were added through online search and have already
changed the route above:

- [MFSPart and MFSPart-Ensemble, TCAD
  2026](https://zhiyaoxie.com/files/TCAD26_MFSPart.pdf): primary generalized
  multi-FPGA partitioning framework.
- [RePart, 2026](https://arxiv.org/abs/2604.00780): logic-replication
  refinement.
- [SHyPar, TCAD 2025/2026](https://arxiv.org/abs/2410.10875): spectral
  coarsening alternative.
- [HySpecPro, 2026](https://arxiv.org/abs/2607.00055): recent GPU spectral
  optimizer; candidate generation only until weighted multi-resource
  legality is supported.
- [Synergistic Die-Level Router, DAC
  2025](https://yibolin.com/publications/papers/ROUTE_DAC2025_Wang.pdf):
  delay-demand-balanced routing and Lagrangian TDM-ratio baseline.
- [Mapping Fusion, 2025](https://arxiv.org/abs/2507.10912): experimental
  synthesis/mapping provider.
- [TD-Placer, 2025](https://arxiv.org/abs/2512.00038): critical-path-aware
  placement candidate.
- [Chimew, FPGA
  2026](https://magic3007.github.io/data/publications/FPGA_FPGA2026_Wang/FPGA_FPGA2026_Wang.pdf):
  primary signal-grouping and pin-assignment route.

Preprints are marked as such and are not treated as established baselines
without equation-level reproduction and independent validation.
