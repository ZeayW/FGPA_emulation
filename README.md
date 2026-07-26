# EmuFlow

EmuFlow is an open, board-abstracted multi-FPGA emulation flow targeting AMD
UltraScale+ devices. The long-term flow covers logic synthesis, partitioning,
board-level routing, TDM scheduling, lane/pin assignment, OpenPARF placement,
FPGA routing, and vendor-assisted bitstream generation.

The repository implements the Phase 1 frontend, an executable Phase 2
physical-backend risk spike, the Phase 3 multi-FPGA partitioner, and Phase 4
board-level system routing:

- versioned EmuIR and Virtual BoardDB formats;
- strict validation without third-party Python dependencies;
- Yosys JSON to EmuIR import;
- UltraScale+ primitive resource classification;
- a runnable Phase 1 pipeline and machine-readable report;
- a virtual two-FPGA `xcvu3p` reference platform.
- ArchitectureDB and placement artifact validation;
- Vivado Site/BEL inventory import for `xcvu3p-ffvc1517-2-e`;
- EmuIR to OpenPARF Bookshelf export;
- OpenPARF `x/y/z` to legal UltraScale+ Site/BEL conversion;
- LOC/BEL XDC generation and a Vivado placement/route validation harness;
- reproducible CPU-only OpenPARF build/run support for `proj169-2`;
- a validated OpenPARF-to-Vivado routed-DCP smoke-test path.
- combinationally safe sequential clustering and hard-macro/group closure;
- deterministic multi-resource partitioning with fixed constraints;
- independent G4 coverage, capacity, constraint, and cut-legality checking.
- directed BoardDB routing with multicast trees and negotiated congestion;
- independent G5 reachability, cycle, latency, direction, and capacity checks.

See [docs/FLOW_PLAN.md](docs/FLOW_PLAN.md) for the complete architecture,
phase boundaries, artifacts, and acceptance criteria. The exact remote
toolchain and observed Phase 2 results are recorded in
[docs/PHASE2_VALIDATION.md](docs/PHASE2_VALIDATION.md).
Larger open-source RTL candidates and pinned fetch instructions are in
[docs/RTL_BENCHMARKS.md](docs/RTL_BENCHMARKS.md). The ordered end-to-end
campaign, from real RTL through forced multi-FPGA TDM and per-FPGA routing, is
defined in
[docs/BENCHMARK_VALIDATION_PLAN.md](docs/BENCHMARK_VALIDATION_PLAN.md).

## Quick start

The checked-in Yosys fixture lets Phase 1 run even when Yosys is not installed:

```bash
PYTHONPATH=src python3 -m emuflow phase1 \
  --yosys-json examples/yosys/counter.json \
  --top counter \
  --clock clk \
  --platform platforms/virtual/xcvu3p_2fpga_p2p.json \
  --out build/phase1-demo
```

Inspect the generated design:

```bash
PYTHONPATH=src python3 -m emuflow ir stats \
  build/phase1-demo/design.emuir.json
```

Validate the virtual platform:

```bash
PYTHONPATH=src python3 -m emuflow platform validate \
  platforms/virtual/xcvu3p_2fpga_p2p.json
```

Run the tests:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## Phase 2 adapter smoke test

The checked-in two-SLICE ArchitectureDB fixture exercises the physical
artifact contracts without requiring Vivado or OpenPARF:

```bash
PYTHONPATH=src python3 -m emuflow phase2 \
  --ir build/phase1-demo/design.emuir.json \
  --arch examples/phase2/xcvu3p_slice_fixture.arch.json \
  --out build/phase2-demo
```

Omitting `--openparf-result` intentionally uses the deterministic reference
placer. It validates the adapter and checker, but is not reported as an
OpenPARF run. To import a real OpenPARF result:

```bash
PYTHONPATH=src python3 -m emuflow phase2 \
  --ir build/phase1-demo/design.emuir.json \
  --arch build/xcvu3p.arch.json \
  --openparf-result results/counter.pl \
  --out build/phase2-openparf
```

## Using a real Yosys installation

Phase 1 can invoke Yosys directly when it is installed:

```bash
PYTHONPATH=src python3 -m emuflow synth-yosys \
  examples/rtl/counter.v \
  --top counter \
  --family xcup \
  --output build/counter.json \
  --log build/counter-yosys.log
```

Then replace `examples/yosys/counter.json` in the Phase 1 command with
`build/counter.json`.

## Running on `proj169-2`

The remote wrapper handles the server's two-hop SSH configuration,
uploads the current committed snapshot, bootstraps a project-local
Yosys, and runs synthesis plus Phase 1:

```bash
scripts/remote/proj169-2.sh probe
scripts/remote/proj169-2.sh all
scripts/remote/proj169-2.sh openparf-sync
scripts/remote/proj169-2.sh openparf-build
scripts/remote/proj169-2.sh phase2-all
scripts/remote/proj169-2.sh serv-l1-all
scripts/remote/proj169-2.sh picorv32-l2-all
```

See [docs/REMOTE_PROJ169_2.md](docs/REMOTE_PROJ169_2.md) for the command
breakdown, remote paths, and environment overrides. The wrapper also
discovers the Vivado 2025.2 installation under
`/data2/vivado/2025.2/Vivado`.

The first real-RTL physical closure result, including SERV resource, routing,
DRC, timing, and current semantic limitations, is recorded in
[docs/SERV_L1_VALIDATION.md](docs/SERV_L1_VALIDATION.md).
The larger 3812-cell PicoRV32 result and the control-set repair boundary are
recorded in
[docs/PICORV32_L2_VALIDATION.md](docs/PICORV32_L2_VALIDATION.md).
The first 100k-scale result, a 121,984-cell PicoRV32 x32 run with a fully
routed and DRC-clean checkpoint, is recorded in
[docs/PICORV32_X32_100K_VALIDATION.md](docs/PICORV32_X32_100K_VALIDATION.md).
The completed Phase 3 implementation and the real two-FPGA partition
experiments are recorded in
[docs/PHASE3_VALIDATION.md](docs/PHASE3_VALIDATION.md).
The completed Phase 4 system router and the 140-net connected-PicoRV32
experiment are recorded in
[docs/PHASE4_VALIDATION.md](docs/PHASE4_VALIDATION.md).

Phase 2 currently uses a conservative physical policy: only the eight `*6LUT`
and eight primary `*FF` BELs in each SLICE are exposed. Paired `*5LUT`,
secondary FF, carry/macro packing, FPGA Interchange physical-netlist patching,
and RapidWright DCP conversion remain explicit follow-on work. The tiny
eight-cell smoke test runs OpenPARF global placement and UltraScale
legalization; its optional ISM detailed-placement pass is disabled because
the upstream implementation assumes a production-scale design.
