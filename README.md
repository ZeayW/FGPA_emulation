# EmuFlow

> [!IMPORTANT]
> ## Open-source source map
>
> Every selected flow engine is stored as editable source and built from this
> repository. This is the compact upstream-source index:
>
> - First-party EmuFlow:
>   [ZeayW/FGPA_emulation](https://github.com/ZeayW/FGPA_emulation)
>   under Apache-2.0
> - Synthesis: [Yosys](https://github.com/YosysHQ/yosys) and
>   [ABC](https://github.com/YosysHQ/abc), including
>   [cxxopts](https://github.com/jarro2783/cxxopts) and
>   [MiniSat](https://github.com/niklasso/minisat)
> - Timing and partitioning:
>   [OpenROAD](https://github.com/The-OpenROAD-Project/OpenROAD),
>   [OpenSTA](https://github.com/The-OpenROAD-Project/OpenSTA), and
>   [RePart](https://github.com/Welement-zyf/RePart), with OpenROAD's retained
>   [ABC](https://github.com/The-OpenROAD-Project/abc),
>   [FastRoute](https://github.com/The-OpenROAD-Project/OpenROAD/tree/master/src/grt/src/fastroute),
>   [Flute3](https://github.com/The-OpenROAD-Project/OpenROAD/tree/master/src/stt/src/flt),
>   [Munkres](https://github.com/The-OpenROAD-Project/OpenROAD/tree/master/src/ppl/src/munkres),
>   and [Material Design Icons](https://github.com/google/material-design-icons)
> - Placement: [OpenPARF](https://github.com/PKU-IDEA/OpenPARF)
> - Default open academic backend:
>   [VTR/VPR](https://github.com/verilog-to-routing/vtr-verilog-to-routing)
>   editable pack/place/route source plus the flagship heterogeneous XML,
>   pinned by commit and SHA-256; materialized dependencies are
>   [pugixml](https://github.com/zeux/pugixml),
>   [libsdcparse](https://github.com/verilog-to-routing/libsdcparse) and
>   [yaml-cpp](https://github.com/jbeder/yaml-cpp)
> - Architecture interchange:
>   [FPGA Interchange Schema](https://github.com/chipsalliance/fpga-interchange-schema),
>   [Cap'n Proto](https://github.com/capnproto/capnproto), and the required
>   [capnproto-java schema](https://github.com/capnproto/capnproto-java)
>   ([RapidWright](https://github.com/Xilinx/RapidWright) is an optional
>   DeviceResources producer, not an open EmuFlow engine, because its current
>   API-library dependency includes Xilinx-EULA-governed material)
> - Decision diagrams: [CUDD](https://github.com/ivmai/cudd)
> - OpenPARF bundled source:
>   [Ccache.cmake](https://github.com/TheLartians/Ccache.cmake),
>   [Blend2D](https://github.com/blend2d/blend2d),
>   [GoogleTest](https://github.com/google/googletest),
>   [LEMON](https://github.com/The-OpenROAD-Project/lemon-graph),
>   [pugixml](https://github.com/zeux/pugixml),
>   [pybind11](https://github.com/pybind/pybind11),
>   [rapidcsv](https://github.com/d99kris/rapidcsv), and
>   [yaml-cpp](https://github.com/jbeder/yaml-cpp)
> - Retained disabled OpenPARF router source:
>   [clipp](https://github.com/muellan/clipp),
>   [gdstk](https://github.com/heitzmann/gdstk),
>   [Qhull](https://github.com/qhull/qhull),
>   [Clipper](https://sourceforge.net/projects/polyclipping/), and
>   [Taskflow](https://github.com/taskflow/taskflow)
> - External build/runtime dependencies:
>   [CMake](https://github.com/Kitware/CMake),
>   [GNU Make](https://git.savannah.gnu.org/cgit/make.git),
>   [GCC](https://github.com/gcc-mirror/gcc) or
>   [LLVM/Clang](https://github.com/llvm/llvm-project),
>   [Python](https://github.com/python/cpython),
>   [Boost](https://github.com/boostorg/boost),
>   [Bison](https://git.savannah.gnu.org/cgit/bison.git),
>   [Flex](https://github.com/westes/flex),
>   [Tcl](https://github.com/tcltk/tcl),
>   [SWIG](https://github.com/swig/swig),
>   [Eigen](https://gitlab.com/libeigen/eigen),
>   [zlib](https://github.com/madler/zlib),
>   [spdlog](https://github.com/gabime/spdlog),
>   [LEMON](https://github.com/The-OpenROAD-Project/lemon-graph),
>   [OR-Tools](https://github.com/google/or-tools),
>   [PyTorch](https://github.com/pytorch/pytorch),
>   [NumPy](https://github.com/numpy/numpy),
>   [PyYAML](https://github.com/yaml/pyyaml),
>   [Hummingbird](https://github.com/microsoft/hummingbird),
>   [NetworkX](https://github.com/networkx/networkx), and
>   [tqdm](https://github.com/tqdm/tqdm)
> - RTL benchmarks:
>   [SERV](https://github.com/olofk/serv),
>   [PicoRV32](https://github.com/YosysHQ/picorv32),
>   [secworks AES](https://github.com/secworks/aes),
>   [Ibex](https://github.com/lowRISC/ibex),
>   [VTR/Koios](https://github.com/verilog-to-routing/vtr-verilog-to-routing),
>   [VeeR EH1](https://github.com/chipsalliance/Cores-VeeR-EH1), and
>   [NVDLA](https://github.com/nvdla/hw)
> - CI:
>   [actions/checkout](https://github.com/actions/checkout) and
>   [actions/setup-python](https://github.com/actions/setup-python)
>
> See the **[complete source, revision, and license inventory](OPEN_SOURCE_COMPONENTS.md)**
> for every nested component and dependency. The corresponding
> [machine-readable inventory](OPEN_SOURCE_COMPONENTS.json) is enforced by CI.

EmuFlow is a research-oriented, open multi-FPGA emulation flow. Its purpose is
to compile one synchronous RTL design into multiple FPGA implementations and a
deterministic communication fabric while keeping every stage inspectable,
replaceable, and independently verifiable.

The default research backend uses a fully public VTR academic architecture
model; no commercial board, FPGA database, or Vivado installation is required.
Real-device backends are adapters behind the same artifacts: ECP5 is the
planned open hardware smoke backend, while UltraScale+/Vivado remains an
optional implementation and sign-off path.

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
| Architecture database | In-tree C++ VTR XML importer; optional FPGA Interchange C++ importer | The default open VTR path imports layout, heterogeneous primitive capacity, primitive/interconnect arcs, switches, segments, and directs into provider-neutral ArchitectureDB/TimingDB artifacts; VPR consumes the original XML for exact mode-aware packing |
| Synthesis/import | In-tree Yosys/ABC plus EmuIR importer | The public VTR flagship profile maps LUT6/DFF logic, 9/18/36-bit multiplier modes, and inferred synchronous single/dual-port RAM modes from repository source |
| Static timing | In-tree standalone OpenSTA plus provider-neutral Architecture TimingDB | The public VTR TimingDB is translated into design-specialized Liberty for LUT/FF/multiplier/RAM primitives; a source-qualified pre-placement sink-delay model combines block arcs with switch/segment RC, and OpenSTA paths drive timing-weighted partitioning |
| Partitioning | In-tree OpenROAD/TritonPart and RePart | Default providers build and run repository source |
| System routing | In-tree C++ route/TDM co-optimization kernel plus independent checker | Default academic provider builds and runs repository source |
| TDM | In-tree C++17 KKT ratio optimizer plus exact scheduler/checker | Default academic provider for timing-annotated routes |
| Netlist/transport | In-tree generator, RTL, simulator, and checker | Working source implementation |
| Pin planning | In-tree C++17 grouping plus sparse min-cost-flow package-pin binding | Virtual planning and synthetic-BSP validation work; real board sign-off awaits a BSP |
| Placement | Root-built OpenPARF plus EmuFlow adapters/checker | VPR packing decisions enter a checked cluster contract; OpenPARF performs analytical global placement and architecture-defined single-site min-cost-flow legalization, followed by independent site/capacity/collision checking and VPR `.place` emission |
| FPGA routing | Root-built VTR/VPR plus independent in-tree C++ artifact checker | The checked OpenPARF placement drives timing-aware detailed routing; the checker independently validates every route node, RR edge/switch, tree branch, net/sink, placement hash, and shared-resource capacity against the exported RR graph |
| Proprietary sign-off | Optional Vivado scripts | Comparison/sign-off only; not part of the open implementation |
| Hardware BSP | In-tree contract | Pending board selection |

`emuflow multi-fpga compile` is the board-independent multi-FPGA integration
gate. Its default public VTR mapping preserves multiplier and synchronous
single/dual-port RAM hard blocks while mapping remaining logic to LUT6/FF. It
then binds EmuIR import, partitioning, system routing, TDM scheduling,
per-FPGA splitting, transport generation, independent checks, and
cycle-equivalence in one report.

`emuflow vpr fpga-open` is the separate integration gate for one FPGA's open
physical backend. It binds synthesis, baseline VPR packing and
auto-layout sizing, ArchitectureDB/TimingDB import, OpenPARF placement, final
VPR routing, and the independent route checker in one versioned report.

The open heterogeneous OpenPARF-to-VPR placement-and-routing path is
implemented for the pinned VTR flagship profile: VTR architecture import,
LUT6/DFF plus multiplier/RAM mapping, exact VPR packing, the checked
packed-cluster contract, OpenPARF placement, VPR placement handoff, detailed
routing, timing analysis, and independent route/RR-graph verification.
Additional architecture mapping profiles and post-placement timing
back-annotation remain open gates.
EmuFlow also does not claim an open UltraScale+ bitstream flow. Vivado may be
used to compare results or generate a bitstream, but success in Vivado cannot
satisfy the default open-flow completion gate.

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

## Build

The supported entry point is the root CMake project. The default `release`
preset builds the first-party C++ kernels and all selected in-tree engines:
Yosys/ABC, CUDD, standalone OpenSTA, RePart, OpenROAD/TritonPart, OpenPARF,
VTR/VPR, and the VTR and FPGA Interchange ArchitectureDB importers.
It does not download any flow engine or install a precompiled provider.

All builds require:

- CMake 3.20 or newer, GNU Make, and a C++17 compiler;
- Python 3.9 or newer as the orchestration and checker runtime; and
- Boost `system`, `thread`, and `serialization`.

The complete default build additionally needs the development packages used by
OpenROAD and OpenPARF: Bison, Flex, Tcl, SWIG 4, Eigen3, zlib, spdlog, LEMON,
OR-Tools C++, OpenMP, PyTorch, NumPy, PyYAML, and Hummingbird. CUDA is optional
and disabled by default. GUROBI is not required because OpenPARF's experimental
router is disabled.

Configure, compile, and test from the repository root:

```bash
cmake --preset release
cmake --build --preset release --parallel
ctest --preset release
```

The build is self-contained below `build/native/`. Its main products are:

```text
build/native/install/bin/emuflow
build/native/install/bin/emuflow_vtr_arch_importer
build/native/install/bin/emuflow_fpgaif_arch_importer
build/native/install/bin/yosys
build/native/install/bin/yosys-abc
build/native/install/bin/vpr
build/native/install/bin/repart
build/native/install/bin/sta
build/native/install/bin/openroad
build/native/install/bin/emuflow_tlr_router
build/native/install/bin/emuflow_tdm_ratio_optimizer
build/native/install/bin/emuflow_tdm_partition_feedback
build/native/install/bin/emuflow_pin_planner
build/native/install/bin/emuflow_bsp_pin_solver
build/native/install/openparf/
```

Dependencies installed in a non-system prefix can be exposed without changing
the source tree:

```bash
cmake --preset release \
  -DEMUFLOW_CMAKE_PREFIX_PATH=/absolute/path/to/dependency-prefix \
  -DEMUFLOW_OPENPARF_PYTHON=/absolute/path/to/python
```

The selected Python must be the interpreter that can import PyTorch. Set
`EMUFLOW_OPENPARF_ENABLE_CUDA=ON` only when that PyTorch installation and the
CUDA toolkit are compatible. See the upstream links and license information in
[Open-source components and provenance](OPEN_SOURCE_COMPONENTS.md).

For fast work on EmuFlow's first-party kernels and artifact contracts, a
developer may explicitly disable the large imported engines. This is a partial
developer build, not the source-complete release configuration:

```bash
cmake -S . -B build/core -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=ON \
  -DEMUFLOW_BUILD_YOSYS=OFF \
  -DEMUFLOW_BUILD_CUDD=OFF \
  -DEMUFLOW_BUILD_REPART=OFF \
  -DEMUFLOW_BUILD_OPENROAD=OFF \
  -DEMUFLOW_BUILD_OPENSTA=OFF \
  -DEMUFLOW_BUILD_VPR=OFF \
  -DEMUFLOW_BUILD_OPENPARF=OFF
cmake --build build/core --parallel
ctest --test-dir build/core --output-on-failure
```

## Quick start

Quick Start uses the CLI produced by the root build; it does not perform an
editable package installation or bypass the installed launcher with a direct
Python module invocation. Add the local build products to `PATH`:

```bash
export PATH="$PWD/build/native/install/bin:$PATH"
emuflow --help
```

Fetch the pinned, SHA-256-verified VTR flagship architecture:

```bash
emuflow arch fetch-default-vtr \
  --output build/architectures/vtr-flagship.xml
```

Compile RTL through the board-independent multi-FPGA flow using the public
academic platform:

```bash
emuflow multi-fpga compile examples/rtl/counter.v \
  --top counter \
  --clock clk \
  --platform platforms/virtual/academic_vtr_2fpga_p2p.json \
  --out build/counter-multi-fpga
```

The command writes a hash-bound `multi-fpga-flow-report.json` only after
partition, route, schedule, split, and cycle-equivalence checks pass. The
default partition provider is the source-built OpenROAD/TritonPart engine.
The default `--mapping-profile vtr-hard-blocks` retains public VTR RAM/DSP
resources. `--mapping-profile generic-soft` is available for architecture-
neutral LUT6/FF experiments, but may expand memory-heavy designs substantially.
For a design that naturally collapses into one zero-cut partition, pass
`--partition-repair-min-used-fpgas`; every repair move remains explicit in the
partition artifact and is checked independently.

Add `--timing-driven --clock-period CLOCK=PERIOD_NS` to make this same command
run OpenSTA, derive timing-critical partition weights, project timing paths
onto the selected cut nets, and drive timing-aware system routing and TDM.
Passing a public VTR TimingDB with
`--architecture-timing-db build/architecture/timing.json` automatically
enables this mode.

For emulation-speed optimization, pass a known-feasible upper bound such as
`--frame-slots 4096 --optimize-frame-slots`. The flow then searches for the
minimum frame that still passes route capacity, ratio legalization, concrete
lane/slot scheduling, precedence, barrier, collision, and transport checks.
Every multi-FPGA run also emits the Phase 7C pausible-clock runtime contract;
its virtual DUT frequency is the fabric frequency divided by the selected
frame length. Original-clock path slack and emulation runtime frequency are
reported separately. For timing-annotated runs, the runtime QoR also checks
the concrete worst scheduled path delay against the selected virtual period;
this remains an academic pre-placement estimate until physical timing is
available.

To validate one FPGA independently with the open physical backend, the
following command fetches the pinned architecture automatically and enables
its multiplier/RAM mapping profile:

```bash
emuflow vpr fpga-open examples/rtl/vtr_hard_blocks.v \
  --top vtr_hard_blocks \
  --out build/vtr-hard-block-flow
```

The per-FPGA command refuses a non-empty output directory and writes a
hash-bound `open-physical-flow-report.json`. Use `--logic-only` for RTL that
must deliberately avoid hard-block inference, or `--architecture` to provide
another VTR XML explicitly.

The equivalent explicit stage commands are shown below for development and
debugging.

Map RTL to VPR-compatible LUT6/DFF eBLIF, then let VPR pack the design and
select the smallest legal auto-layout:

```bash
emuflow vpr synth third_party/rtl/picorv32/picorv32.v \
  --top picorv32 \
  --output build/picorv32.eblif \
  --log build/picorv32-yosys.log

emuflow vpr run \
  --architecture build/architectures/vtr-flagship.xml \
  --circuit build/picorv32.eblif \
  --out build/picorv32-vpr
```

Import an ArchitectureDB with exactly the dimensions recorded by VPR. The
TimingDB retains primitive, interconnect, switch, segment, and direct-delay
data from the same XML:

```bash
emuflow arch import-vtr build/architectures/vtr-flagship.xml \
  --architecture-id vtr-k6-n10-40nm \
  --reference-placement build/picorv32-vpr/picorv32.place \
  --architecture-output build/architectures/picorv32.archdb.json \
  --timing-output build/architectures/picorv32.timing.json

emuflow vpr import-packed \
  --input build/picorv32-vpr/picorv32.net \
  --architecture build/architectures/vtr-flagship.xml \
  --circuit build/picorv32.eblif \
  --output build/picorv32-vpr/packed-contract.json

emuflow vpr place-openparf \
  --packed build/picorv32-vpr/packed-contract.json \
  --architecture-db build/architectures/picorv32.archdb.json \
  --out build/picorv32-openparf

emuflow vpr route-packed \
  --architecture build/architectures/vtr-flagship.xml \
  --circuit build/picorv32.eblif \
  --packed-netlist build/picorv32-vpr/picorv32.net \
  --packed-contract build/picorv32-vpr/packed-contract.json \
  --placement build/picorv32-openparf/picorv32.place \
  --out build/picorv32-openparf-route
```

`vpr run` emits and verifies the packed `.net`, baseline `.place`, detailed
`.route`, console log, and `vpr-report.json`. `import-packed` preserves VPR's
exact cluster modes, pb hierarchy, atom membership, and cross-cluster nets in
a hash-bound versioned contract. `place-openparf` places those exact clusters
and emits a checked VPR placement. `route-packed` routes that placement
without invoking VPR's baseline placer, exports the exact RR graph, and runs
the independent C++ route checker.

To exercise heterogeneous synthesis instead of the logic-only PicoRV32
example, use the checked-in multiplier/RAM fixture and the pinned mapping
profile:

```bash
emuflow vpr synth examples/rtl/vtr_hard_blocks.v \
  --top vtr_hard_blocks \
  --hard-blocks \
  --output build/vtr_hard_blocks.eblif
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

emuflow sta run-opensta \
  --ir build/phase1-demo/design.emuir.json \
  --clock-period clk=10 \
  --output build/phase1-demo/timing-paths.json

emuflow sta derive-partition-net-weights \
  --database build/phase1-demo/timing-paths.json \
  --ir build/phase1-demo/design.emuir.json \
  --output build/phase1-demo/partition-net-weights.json
```

For a VTR-mapped design, pass the imported public timing database with
`--architecture-timing-db build/architecture/timing.json`; the generated
model is an architecture-sourced pre-placement estimate, not routed sign-off.
Pass the resulting weight artifact to Phase 3 with `--net-weights`.
OpenSTA queries up to 200,000 endpoint paths by default and reports
`path_limit_reached`; raise `--max-paths` when that flag is true.
Timing-weighted TritonPart automatically includes a same-seed unweighted
baseline candidate and selects the lowest independently recomputed weighted
cut objective; `--tritonpart-seed-attempts` adds weighted candidates.

The counter fixture avoids requiring synthesis for the first run. The
following command is retained only for the optional UltraScale+/Vivado
compatibility backend:

```bash
emuflow synth-yosys examples/rtl/counter.v \
  --top counter \
  --family xcup \
  --output build/counter.json \
  --log build/counter-yosys.log
```

Import a generated FPGA Interchange DeviceResources file with an explicit
producer declaration, then check a synthesized design against its primitive
and BEL capacity:

```bash
emuflow arch import-fpga-interchange device.device \
  --part xcvu9p-flga2104-2L-e \
  --generator "producer name and exact version" \
  --output build/xcvu9p.archdb.json

emuflow arch check-capacity \
  --arch build/xcvu9p.archdb.json \
  --ir build/phase1-demo/design.emuir.json
```

Use `emuflow --help` and `emuflow <command> --help` for the complete CLI. The
installed `emuflow` launcher intentionally uses the in-tree Python control
plane for orchestration and independent checking; optimization work remains in
the compiled C/C++/CUDA providers listed above. Vivado remains an optional
proprietary validation backend and is not an open-source EmuFlow component.

## Source-complete monorepo

EmuFlow does not publish opaque provider binaries or download flow engines
after checkout. Implementations are editable source in this repository:

- `engines/cudd/`: CUDD decision-diagram source required by OpenSTA;
- `engines/capnproto/` and `engines/fpga-interchange-schema/`: the open
  serializer and schemas used to import FPGA device resources;
- `engines/yosys/`: Yosys synthesis, ABC mapping, and cxxopts source;
- `engines/repart/`: RePart C++ hypergraph partitioner;
- `engines/openroad/`: OpenROAD and TritonPart C++ source;
- `engines/openparf/`: OpenPARF C++/CUDA/Python source;
- `engines/vtr/`: VPR packing, placement, routing-resource graph, detailed
  routing, and materialized dependency source; and
- `src/native/`: first-party C++ optimization kernels, including the
  timing-aware system router, Lagrangian/KKT TDM-ratio optimizer, and
  placement-aware logical-pin and physical package-pin planners;
- `src/emuflow/`: EmuFlow control plane, artifact contracts, native-provider
  adapters, and independent checkers.

RePart is not consumed as a published binary. Its C++ optimization source is
compiled by the root CMake build. EmuFlow's small Python adapter emits the
versioned hypergraph/replicability inputs and independently checks the C++
result; it is not a replacement partitioning algorithm.

The default Phase 4 provider follows the same boundary: the editable C++17
kernel in `src/native/tlr_router.cpp` constructs and refines multicast trees.
Without STA input it runs the native load-balanced mode; with versioned STA
paths it additionally accounts for timing criticality and an analytically
predicted TDM serialization ratio. Python invokes the root-build product and
independently reconstructs topology, capacity, direction locks, delay, the TDM
proxy, slack, and path signatures. The original Python negotiated router is no
longer a runtime provider.

Cross-stage partition/routing/TDM work uses a partition-independent STA path
database. The default provider builds standalone OpenSTA from
`engines/openroad/src/sta`, renders the versioned open FPGA timing model, and
records ordered stable EmuIR net identities for each global path. The Vivado
adapter remains an optional calibration/comparison provider.
`emuflow sta project-path-database` projects the same database onto every
candidate partition's cut nets. Slack normalization is frozen once at
database import, so candidates cannot change either the timing sample or its
scale.

`emuflow cross-stage optimize` closes the checked Phase 3--5 feedback loop:
it derives TDM/channel-pressure weights, reruns the selected source-built
partitioner, projects the frozen timing database, reruns the accepted routing
and scheduling kernels, and applies deterministic lexicographic
accept/rollback. Its independent candidate scorer evaluates every database
path, including paths made local by a candidate partition, from the concrete
lane/slot schedule. Feedback is applied by multiplicative log-space
interpolation, with a deterministic decreasing-step line search; this limits
the discontinuity of a new hypergraph partition and never promotes a
regressing full-step candidate.

With `--optimize-frame-slots`, every partition candidate treats
`--frame-slots` as a feasible upper bound and reruns the checked Phase 4/5
minimum-frame search. The outer-loop objective first minimizes the exact
feasible frame, then maximizes the estimated timing margin against the
pausible virtual-DUT clock. Original RTL-clock slack remains a secondary
research metric and is not used as the emulation closure gate. The report
checker independently rebuilds each candidate score, validates the proven
feasible/infeasible frame boundary, and replays accept/rollback.

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
netlists. Its validation report includes the reconstructed logical-lane
baseline and objective, crossing-bit, and pin-distance improvements.

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

The default ArchitectureDB path is source-complete. The root build compiles
`src/native/vtr_architecture_importer.cpp` against the vendored pugixml source.
It reads public VTR architecture XML and emits provider-neutral physical and
timing artifacts. The checked-in source manifest pins the VTR flagship XML by
upstream commit and SHA-256; the small checked-in XML is only a deterministic
parser regression fixture. Current placement capacity is deliberately a
relaxed maximum over mutually exclusive VTR modes. It must not be confused
with a completed packer.

The optional real-device path compiles
`src/native/fpga_interchange_arch_importer.cpp` against the vendored FPGA
Interchange schema and Cap'n Proto source. A DeviceResources input must declare
its generator because the schema license does not determine the generator's
license. RapidWright may generate or compare such input, but its current
`rapidwright-api-lib` dependency includes Xilinx-EULA-governed material, so it
is an optional input-generation tool and not an EmuFlow open engine.
Repeated BEL inventories are stored once per site template, keeping real VU9P
ArchitectureDB artifacts practical while preserving every physical site.
DSP48E2 and RAM64X1S are recorded as macro resources over their canonical
component BELs; RAMB18E2/RAMB36E2 modes retain their shared-site relationship.
Because DeviceResources v1 does not encode SLR, clock-region, or I/O-bank
membership, EmuFlow does not infer those relations from coordinates. The
versioned physical-region sidecar binds to an exact ArchitectureDB hash; its
in-tree merger requires one unambiguous region assignment for every placement
site and independently checks the region hierarchy and package-pin inventory.
The included RapidWright Jython exporter is an optional mixed-license data
adapter, not an open engine, and generated device data is not committed.

Each imported tree contains its upstream license, exact commit provenance, and
EmuFlow modification list. No precompiled provider executable, object,
library, or Python extension is checked in.

[`SOURCE_MANIFEST.json`](SOURCE_MANIFEST.json) records each flow implementation
path, root build target, local runtime product, integration state, and
remaining open-path blocker. [`OPEN_SOURCE_COMPONENTS.json`](OPEN_SOURCE_COMPONENTS.json)
is the machine-readable provenance inventory; its human-readable companion is
[Open-source components and provenance](OPEN_SOURCE_COMPONENTS.md).

Build products are written below `build/` and are never the source of truth.
Developers can edit any in-tree C++ implementation and rebuild through the
same top-level command.

The `source-complete` GitHub Actions gate rejects tracked executables,
libraries, objects, bitstreams, Git LFS pointers, submodules, and incomplete
provider source trees. It also compiles every first-party C++ provider and
runs the independent artifact/checker test suite.

The repository includes the source of every currently selected flow engine.
A compiler, CMake, Python, and general-purpose libraries such as Boost,
PyTorch, Tcl, SWIG, Protobuf, and OR-Tools remain build dependencies; they are
not opaque replacements for an EmuFlow stage. The build never downloads a
partitioner, placer, router, or synthesis executable.

OpenPARF's optional experimental `fpga-router` is not a selected flow engine:
its upstream build currently requires proprietary GUROBI, so the root build
excludes it and the release gate cannot count it. Integrating an open detailed
router for the default VTR architecture remains an explicit project blocker
rather than a hidden binary dependency.

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
engines/           root-built Yosys, OpenROAD, RePart, OpenPARF, VPR, and CUDD source
third_party/       external RTL benchmarks and retained upstream patch records
tests/             unit, adversarial, and flow-level regression tests
docs/              architecture, algorithm, and benchmark plans
```

## Documentation

- [Flow architecture and phase contracts](docs/FLOW_PLAN.md)
- [Academic algorithm upgrade plan](docs/ALGORITHM_UPGRADE_PLAN.md)
- [Open-source components and provenance](OPEN_SOURCE_COMPONENTS.md)

Machine-specific configurations, raw results, QoR tables, and experiment
notes are intentionally kept outside the repository.

## Development status

EmuFlow is an active research prototype. The Phase 3–6 academic providers are
kept behind independent artifact checkers and deterministic promotion gates.
The current campaign evaluates their checked Phase 3--5 outer feedback loop;
cross-stage behavior is promoted only after small, medium, and large
real-design comparisons against the frozen single-stage flow.
