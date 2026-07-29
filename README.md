# EmuFlow

EmuFlow is a research-oriented, open multi-FPGA emulation flow for AMD
UltraScale+ devices. Its purpose is to compile one synchronous RTL design into
multiple FPGA implementations and a deterministic communication fabric while
keeping every stage inspectable, replaceable, and independently verifiable.

The project targets a source-complete path from logic synthesis to per-FPGA
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

| Stage | Implementation source | Honest integration status |
| --- | --- | --- |
| Synthesis/import | In-tree Yosys/ABC plus EmuIR importer | Default path builds and runs repository source |
| Partitioning | In-tree OpenROAD/TritonPart and RePart | Default providers build and run repository source |
| System routing | In-tree C++ route/TDM co-optimization kernel plus independent checker | Default academic provider builds and runs repository source |
| TDM | In-tree C++17 KKT ratio optimizer plus exact scheduler/checker | Default academic provider for timing-annotated routes |
| Netlist/transport | In-tree generator, RTL, simulator, and checker | Working source implementation |
| Pin planning | In-tree C++17 grouping plus sparse min-cost-flow package-pin binding | Virtual planning and synthetic-BSP validation work; real board sign-off awaits a BSP |
| Placement | Root-built OpenPARF plus EmuFlow adapters/checker | Default path builds, runs, and independently validates repository source |
| FPGA routing | Provider not yet selected | Open device/timing database and detailed router remain blockers |
| Proprietary sign-off | Optional Vivado scripts | Comparison/sign-off only; not part of the open implementation |
| Hardware BSP | In-tree contract | Pending board selection |

Individual board-independent stages have been exercised from small counter
and CPU designs through a connected 731,313-cell NVDLA partition. Those
experiments validate stage contracts, but do **not** yet prove that a clean
checkout can execute the entire open placement-and-routing path with one
command. That end-to-end source-build gate remains open.

EmuFlow is not yet a fully open UltraScale+ physical implementation or
bitstream flow. The complete public UltraScale+ routing-resource/timing
database and open bitstream generator needed for that claim are not currently
part of this repository. Vivado may be used to compare results or generate a
bitstream, but success in Vivado cannot satisfy an open-flow completion gate.

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

EmuFlow does not publish opaque provider binaries or download flow engines
after checkout. Implementations are editable source in this repository:

- `third_party/cudd/`: CUDD decision-diagram source required by OpenSTA;
- `third_party/yosys/`: Yosys synthesis, ABC mapping, and cxxopts source;
- `third_party/repart/`: RePart C++ hypergraph partitioner;
- `third_party/openroad/`: OpenROAD and TritonPart C++ source;
- `third_party/openparf/`: OpenPARF C++/CUDA/Python source; and
- `src/native/`: first-party C++ optimization kernels, including the
  timing-aware system router, Lagrangian/KKT TDM-ratio optimizer, and
  placement-aware logical-pin and physical package-pin planners;
- `src/emuflow/`: EmuFlow control plane, artifact contracts, baseline
  implementations, adapters, and independent checkers.

RePart is not consumed as a published binary. Its C++ optimization source is
compiled by the root CMake build. EmuFlow's small Python adapter emits the
versioned hypergraph/replicability inputs and independently checks the C++
result; it is not a replacement partitioning algorithm.

The default academic Phase 4 provider follows the same boundary: the editable
C++17 kernel in `src/native/tlr_router.cpp` jointly accounts for route delay,
timing criticality, negotiated congestion, and an analytically predicted TDM
serialization ratio while constructing and refining multicast trees. Python
imports versioned STA paths, invokes the root-build product, and independently
reconstructs topology, capacity, direction locks, delay, the TDM proxy, slack,
and path signatures. The earlier route-only provider remains available for
controlled comparisons. A provider-neutral checker evaluates negotiated and
timing-aware routes against the same normalized STA artifact and reports
absolute-slack and normalized-slack extrema separately across clock domains.

The academic Phase 5 provider is likewise rooted in editable C++17 source at
`src/native/tdm_ratio_optimizer.cpp`. The Python layer constructs the
versioned timing model, realizes the optimized ratio/lane groups as an exact
slot schedule, and independently checks capacity, ratio legality, timing,
collisions, precedence, round barriers, and transported values.
For an apples-to-apples QoR comparison, it additionally reconstructs path
delay from each concrete scheduled wait for both the baseline and academic
providers; ratio-based slack is reported separately as a conservative bound.

Phase 6A uses the same source boundary. The C++17 planner at
`src/native/placement_aware_pin_planner.cpp` forms the minimum feasible number
of homogeneous TDM groups, improves their placement-region and endpoint
dispersion costs by deterministic swaps, and solves group-to-virtual-pin
matching exactly with the Hungarian algorithm. The Python layer derives
lookahead coordinates from OpenPARF, materializes the plan, and independently
reconstructs group capacity, slot collisions, objective values, and split
netlists.

Phase 6B is also source-complete: `src/native/bsp_pin_solver.cpp` implements
exact sparse minimum-cost bipartite flow over electrically legal physical
channels. The checker independently enforces pin uniqueness, directed
connectivity, bank capacity, bank/pin IOSTANDARD support, reserved pins,
frequency limits, and binding cost before emitting per-FPGA XDC. The checked-in
explicit VU9P mesh BSP is deliberately synthetic and is only an algorithm
validation target; a real board still requires revision-controlled pin data
and vendor DRC/timing sign-off.

FPGA placement follows the same rule. The default Phase 2/7 path launches the
OpenPARF Python, C++, and PyTorch-operator source compiled by the root CMake
build, then independently reloads and checks every Site/BEL assignment against
the ArchitectureDB. Importing an externally generated `.pl` file remains
available only as an explicitly labelled comparison path and cannot pass the
source-complete release gate.

Each imported tree contains its upstream license, exact commit provenance, and
EmuFlow modification list. No precompiled provider executable, object,
library, or Python extension is checked in.

[`SOURCE_MANIFEST.json`](SOURCE_MANIFEST.json) is the machine-readable source
inventory. It records each implementation path, root build target, local
runtime product, integration state, and remaining open-path blocker.

Configure and build all currently integrated open components from the
repository root:

```bash
cmake --preset release
cmake --build --preset release --parallel
ctest --preset release
```

Build products are written below `build/` and are never the source of truth.
Developers can edit any in-tree C++ implementation and rebuild through the
same top-level command.

The repository includes the source of every currently selected flow engine.
A compiler, CMake, Python, and general-purpose libraries such as Boost,
PyTorch, Tcl, SWIG, Protobuf, and OR-Tools remain build dependencies; they are
not opaque replacements for an EmuFlow stage. The build never downloads a
partitioner, placer, router, or synthesis executable.

OpenPARF's optional experimental `fpga-router` is not a selected flow engine:
its upstream build currently requires proprietary GUROBI, so the root build
excludes it and the release gate cannot count it. Selecting or reproducing an
open detailed UltraScale+ router, together with an open device/timing database,
remains an explicit project blocker rather than a hidden binary dependency.

This distinction is deliberate: a C++ provider runs as a compiled executable,
but that executable is disposable output below `build/`. The editable
implementation is its tracked C++/CUDA source, built by the root CMake graph.
An externally supplied executable may be used only for an explicitly labelled
comparison experiment and is never the default provider.

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
