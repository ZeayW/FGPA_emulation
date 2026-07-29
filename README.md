# EmuFlow

EmuFlow is a research-oriented, open multi-FPGA emulation flow for AMD
UltraScale+ devices. Its purpose is to compile one synchronous RTL design into
multiple FPGA implementations and a deterministic communication fabric while
keeping every stage inspectable, replaceable, and independently verifiable.

The project targets the complete path from logic synthesis to per-FPGA
placement and routing:

```text
RTL
 └─ logic synthesis and EmuIR import
     └─ sequential clustering and multi-resource partitioning
         └─ board-level system routing
             └─ TDM ratio, slot, and lane assignment
                 └─ per-FPGA netlist and transport generation
                     └─ logical/physical pin planning
                         └─ OpenPARF placement
                             └─ FPGA routing and implementation validation
```

The flow is board-abstracted. Synthesis, partitioning, routing, TDM, logical
pin assignment, and virtual-platform physical validation can run before a
board is selected. Package-pin binding, board clocks, shell integration,
bitstream generation, and hardware bring-up require a concrete board support
package.

## Why EmuFlow

Commercial prototyping tools tightly couple their intermediate formats and
optimization engines. EmuFlow instead uses versioned artifacts and independent
checkers between stages. This makes it possible to:

- study partitioning, system routing, TDM, pin assignment, and placement as
  separate optimization problems;
- compare academic algorithms without changing the rest of the flow;
- reproduce quality-of-result experiments on real RTL designs;
- validate feasibility and semantics independently of the optimizer that
  produced a result; and
- add a board later without rebuilding the board-independent frontend.

## Current scope

The current semantic model supports a single virtual DUT clock, synchronous
reset, deterministic static communication schedules, and lockstep execution
with a global frame barrier. Partition cuts are restricted to safe sequential
boundaries; combinational loops and hard macros remain atomic.

| Stage | Current implementation | Status |
| --- | --- | --- |
| Synthesis/import | Yosys JSON to versioned EmuIR | Implemented |
| Partitioning | Greedy baseline, TritonPart, RePart partitioning and logic replication | Implemented; Phase 3B validated |
| System routing | Negotiated baseline plus in-tree C++ timing-aware load-balanced routing | Academic provider implemented; real-STA promotion in progress |
| TDM | Legal lane/slot scheduling with precedence and collision checks | Implemented; academic upgrade planned |
| Netlist/transport | Per-FPGA split, shadow endpoints, generated TDM RTL | Implemented |
| Pin planning | Logical lanes and virtual I/O anchors | Implemented |
| Placement | OpenPARF global placement plus UltraScale+ legalization | Implemented |
| FPGA routing | Vivado validation backend | Implemented |
| Hardware BSP | Versioned requirements contract | Pending board selection |

The board-independent flow has been exercised from small counter and CPU
designs through a connected 731,313-cell NVDLA partition. Promotion gates
include deterministic partitioning, independent legality checks, transport
simulation, OpenPARF placement, and routed-checkpoint validation.

EmuFlow is not yet a fully open UltraScale+ bitstream flow. Yosys, the
partitioners, EmuFlow's system algorithms, and OpenPARF are open-source;
Vivado currently remains the routing/bitstream validation backend.

## Design principles

- **Versioned boundaries:** EmuIR, BoardDB, ArchitectureDB, placement,
  transport, lane-map, and BSP artifacts have explicit schemas.
- **Independent correctness gates:** coverage, capacity, cut legality,
  reachability, link capacity, scheduling, placement, routing, and cycle
  behavior are checked separately from optimization.
- **Provider-based algorithms:** optimization engines can be replaced while
  preserving the surrounding artifact contracts and checkers.
- **Deterministic experiments:** fixed inputs and seeds produce auditable
  outputs with tool revisions, configurations, hashes, runtime, and memory.
- **Board abstraction:** logical communication planning is separated from
  package pins and hardware-specific shell constraints.

## Quick start

EmuFlow requires Python 3.9 or newer and has no mandatory third-party Python
dependencies for its core artifact and checker path.

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
python3 -m unittest discover -s tests -v
```

Run the checked-in board-independent counter example:

```bash
emuflow phase1 \
  --yosys-json examples/yosys/counter.json \
  --top counter \
  --clock clk \
  --platform platforms/virtual/xcvu3p_2fpga_p2p.json \
  --out build/phase1-demo

emuflow ir stats build/phase1-demo/design.emuir.json
```

The fixture avoids requiring Yosys for the first run. To synthesize RTL
directly:

```bash
emuflow synth-yosys examples/rtl/counter.v \
  --top counter \
  --family xcup \
  --output build/counter.json \
  --log build/counter-yosys.log
```

Use `emuflow --help` and `emuflow <command> --help` for the complete CLI.
Vivado remains an optional proprietary validation backend; it is not an
open-source EmuFlow component.

## Source-complete monorepo

EmuFlow does not publish opaque provider binaries or require source downloads
after checkout. The implementation source used by the flow is present in this
repository:

- `third_party/yosys/`: Yosys synthesis, ABC mapping, and cxxopts source;
- `third_party/repart/`: RePart C++ hypergraph partitioner;
- `third_party/openroad/`: OpenROAD and TritonPart C++ source;
- `third_party/openparf/`: OpenPARF C++/CUDA/Python source; and
- `src/native/`: first-party C++ optimization kernels, including the
  timing-aware system router; and
- `src/emuflow/`: EmuFlow control plane, artifact contracts, baseline
  implementations, adapters, and independent checkers.

RePart is not consumed as a published binary. Its C++ optimization source is
compiled by the root CMake build. EmuFlow's small Python adapter emits the
versioned hypergraph/replicability inputs and independently checks the C++
result; it is not a replacement partitioning algorithm.

The academic Phase 4 provider follows the same boundary: the TLR/TRR
optimization kernel is editable C++17 source in `src/native/tlr_router.cpp`.
Python imports versioned STA paths, invokes the root-build product, and
independently reconstructs topology, capacity, direction-lock, delay, slack,
and path-signature results.

Each imported tree contains its upstream license, exact commit provenance, and
EmuFlow modification list. No precompiled provider executable, object,
library, or Python extension is checked in.

Configure and build all open components from the repository root:

```bash
cmake --preset release
cmake --build --preset release --parallel
ctest --preset release
```

Build products are written below `build/` and are never the source of truth.
Developers can edit any in-tree C++ implementation and rebuild through the
same top-level command.

The repository includes every direct flow-engine implementation. A compiler,
CMake, Python, and general-purpose build libraries such as Boost, PyTorch, Tcl,
SWIG, Protobuf, and OR-Tools remain declared build dependencies; they are not
opaque replacements for an EmuFlow stage. The build never downloads a
partitioner, placer, router, or synthesis executable.

## Repository layout

```text
src/emuflow/       flow implementations, providers, and independent checkers
schemas/           versioned artifact schemas
platforms/         board-independent virtual multi-FPGA platforms
rtl/transport/     reusable TDM and barrier RTL
benchmarks/        benchmark catalog and run configurations
examples/          small reproducible RTL and artifact fixtures
scripts/           provider integration and reusable flow utilities
third_party/       in-tree Yosys/ABC, OpenPARF, OpenROAD, and RePart source
tests/             unit, adversarial, and flow-level regression tests
docs/              architecture, algorithm, and benchmark plans
```

## Documentation

- [Flow architecture and phase contracts](docs/FLOW_PLAN.md)
- [Academic algorithm upgrade plan](docs/ALGORITHM_UPGRADE_PLAN.md)

Machine-specific configurations, raw results, QoR tables, and experiment
notes are intentionally kept outside the repository.

## Development status

EmuFlow is an active research prototype. The current campaign is replacing
the Phase 3–6 optimization cores with stronger academic algorithms one stage
at a time. A provider is promoted only after deterministic real-design
evaluation, independent correctness checks, downstream validation, and a
recorded QoR comparison. Cross-stage co-optimization is intentionally deferred
until those individual upgrades are complete.
