# EmuFlow architecture and implementation plan

## 1. Goal and scope

EmuFlow compiles a synchronous RTL design into multiple AMD UltraScale+ FPGA
implementations connected by statically scheduled board links. The target
implementation is open through synthesis, partitioning, system routing, TDM,
pin planning, placement, and optionally routing. Vivado remains the validation
and bitstream backend until an open UltraScale+ bitstream generator is viable.

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
UltraScale+ packing and FPGA Interchange
        |
        v
OpenPARF placement
        |
        v
Vivado / RWRoute / OpenPARF FPGA routing
        |
        v
Validated DCP and Vivado bitstream
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
  -> fpga_N/routed.dcp
  -> fpga_N/design.bit
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
- UltraScale+ resource classification;
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

### Phase 2 — UltraScale+ physical-backend risk spike (in progress)

The first executable increment implements:

- ArchitectureDB v1 and Placement v1;
- Vivado Site/BEL inventory to ArchitectureDB;
- EmuIR to OpenPARF Bookshelf;
- OpenPARF `x/y/z` result to legal UltraScale+ Site/BEL placement;
- one-instance/one-BEL, compatibility, completeness, and collision checks;
- LOC/BEL XDC generation;
- a real `xcvu3p` Vivado DCP/route validation harness.

The initial compatibility policy exposes only `*6LUT` and primary `*FF` BELs.
This is intentionally conservative: it avoids accepting a placement that
requires LUT input sharing or control-set repair that the flow does not yet
implement.

The remainder of Phase 2 uses public `xcvu3p` FPGA Interchange collateral to
implement:

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

### Phase 3 — Sequential clustering and partitioning

Implement:

- combinational SCC detection;
- sequential-cone clustering;
- carry, DSP, BRAM/URAM cascade grouping;
- fixed/group constraints for hard IP and board shells;
- TritonPart adapter with multi-dimensional resource weights;
- topology-aware refinement and utilization headroom.

Acceptance:

- every primitive belongs to exactly one partition;
- all group and fixed constraints hold;
- no forbidden combinational cut exists;
- every FPGA satisfies its effective resource capacities;
- cut and timing metrics are reproducible for a fixed seed.

### Phase 4 — Board-level system routing

Implement a negotiated-congestion router over BoardDB:

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

### Phase 5 — TDM scheduling and cycle-accurate transport

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

### Phase 6 — Per-FPGA netlist generation and lane/pin planning

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

### Phase 7 — Integrated placement, routing, and bitstream

Implement provider interfaces for:

- Vivado routing baseline;
- RWRoute;
- OpenPARF FPGA'24-style router;
- Vivado route/DRC/timing validation;
- vendor-assisted bitstream generation.

Acceptance:

- all per-FPGA designs route;
- Vivado reports legal routes and passes DRC;
- setup/hold and board-interface timing are reported separately;
- reproducible QoR reports include placement, route, TDM, and emulation speed.

### Phase 8 — Open synthesis/packing completion and hardware bring-up

Replace bootstrap mapped DCPs with:

```text
Yosys synth_xilinx -family xcup
  -> UltraScale+ site packer
  -> FPGA Interchange logical/partial physical netlist
```

Add real-board link training, PRBS, deskew, barrier diagnostics, host control,
and golden-workload testing.

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

- part: `xcvu3p-ffvc1517-2-e`;
- virtual platform: two-FPGA point-to-point;
- per-FPGA utilization limit: 75%;
- logical link: 32 lanes per direction at 250 MHz;
- modeled link latency: two fabric cycles;
- physical mode: out-of-context, no package-pin binding;
- placement: OpenPARF target;
- routing baseline: Vivado, followed later by RWRoute/OpenPARF.

The device capacities in the virtual platform are planning values. Phase 2 will
replace them with values derived from the selected FPGA Interchange
DeviceResources file.
