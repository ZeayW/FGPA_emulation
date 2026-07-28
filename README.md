# EmuFlow

EmuFlow is an open, board-abstracted multi-FPGA emulation flow targeting AMD
UltraScale+ devices. The long-term flow covers logic synthesis, partitioning,
board-level routing, TDM scheduling, lane/pin assignment, OpenPARF placement,
FPGA routing, and vendor-assisted bitstream generation.

The repository implements the board-independent path through Phase 7C plus
the Phase 7D reproducible release audit:
frontend synthesis/import, multi-FPGA partitioning, system routing, TDM,
per-FPGA transport generation, OpenPARF placement, Vivado routing, and the
virtual runtime/timing contract:

- versioned EmuIR and Virtual BoardDB formats;
- strict validation without third-party Python dependencies;
- Yosys JSON to EmuIR import;
- UltraScale+ primitive resource classification;
- a runnable Phase 1 pipeline and machine-readable report;
- virtual two-FPGA and eight-FPGA-mesh `xcvu3p` reference platforms;
- a four-FPGA `xcvu9p` mesh with 25% physical-implementation headroom;
- ArchitectureDB and placement artifact validation;
- Vivado Site/BEL inventory import for `xcvu3p-ffvc1517-2-e`;
- EmuIR to OpenPARF Bookshelf export;
- OpenPARF `x/y/z` to legal UltraScale+ Site/BEL conversion;
- LOC/BEL XDC generation and a Vivado placement/route validation harness;
- reproducible CPU-only OpenPARF build/run support for `proj169-2`;
- a validated OpenPARF-to-Vivado routed-DCP smoke-test path.
- combinationally safe sequential clustering and hard-macro/group closure;
- OpenROAD/TritonPart multilevel hypergraph partitioning with cell/LUT/FF
  weights, fixed constraints, and deterministic fixed-seed execution;
- a dependency-free greedy partitioner retained only as a fallback/baseline;
- independent G4 coverage, capacity, constraint, and cut-legality checking.
- directed BoardDB routing with multicast trees and negotiated congestion;
- independent G5 reachability, cycle, latency, direction, and capacity checks.
- latency-aware lane/slot scheduling and independent collision/precedence checks;
- generic TDM link/barrier RTL plus generated schedule-specific simulation.
- exact per-FPGA logical netlists with cut-net shadow endpoints;
- two-ended logical lane maps and virtual IO-region anchors;
- generated per-FPGA transport mux/capture RTL;
- mapped LUT/FF cycle-equivalence checking across the partition boundary.
- real transport synthesis and per-FPGA placement-IR stitching;
- independent OpenPARF placement and Site/BEL legality for both partitions.
- structural primitive-netlist emission and routed-DCP validation;
- integrated lockstep barrier controllers and pausible-clock semantics;
- separate DUT, fabric, and fabric-to-DUT timing gates;
- machine-readable end-to-end physical and emulation QoR.
- a reproducible G0-G9 release manifest with source/artifact hashing.

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
scripts/remote/proj169-2.sh tritonpart-bootstrap
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
The first genuine connected million-cell screen, the official NVDLA
`NV_nvdla` top with 3,123,117 synthesized cells, and the measured 118 GB
Yosys-JSON importer limit are recorded in
[docs/NVDLA_NVDLAV1_VALIDATION.md](docs/NVDLA_NVDLAV1_VALIDATION.md).
The connected NVDLA CACC partition experiment carries 731,313 EmuIR cells
through TritonPart, system routing, TDM, virtual pin planning, netlist split,
OpenPARF placement, and per-FPGA Vivado implementation. All four routed DCPs
pass the independent Phase 7C gate with 731,387 mapped cells, one recorded
Vivado-inserted BUFGCE, zero unrouted nets, zero DRC violations, and
+1.435 ns worst WNS. Its Phase 7D audit rehashes 376 source dependencies and
26 critical artifacts into a byte-reproducible G0-G9 manifest; exact results
and the board-independent boundary are recorded in
[docs/NVDLA_PARTITION_A_FULL_FLOW.md](docs/NVDLA_PARTITION_A_FULL_FLOW.md).
The follow-on four-FPGA resource-bounded experiment adds register-input
transport rounds, independently rejects infeasible TritonPart solutions,
legalizes the low-cut solution against cell/LUT/FF upper bounds, and validates
142,882 real cut nets through routing, TDM, splitting, four OpenPARF
placements, and four Vivado routed checkpoints. Its final Phase 7C result
covers 1,117,404 mapped cells plus 146 audited timing replicas and one BUFG,
with zero unrouted nets, zero DRC violations, and +0.010 ns worst WNS. Its
Phase 7D audit rehashes 376 source dependencies and 26 critical artifacts;
two independent G0-G9 manifests are byte-identical at commit
`63b05710466d35a64759ae51a1c51772e957c7ab`. Its algorithms, controls, and
measured results are recorded in
[docs/NVDLA_PARTITION_A_BALANCED_FLOW.md](docs/NVDLA_PARTITION_A_BALANCED_FLOW.md).
The intermediate connected VeeR EH1 CPU result is recorded in
[docs/VEER_EH1_VALIDATION.md](docs/VEER_EH1_VALIDATION.md).
The completed Phase 3 implementation and the real two-FPGA partition
experiments are recorded in
[docs/PHASE3_VALIDATION.md](docs/PHASE3_VALIDATION.md).
The completed Phase 4 system router and the 140-net connected-PicoRV32
experiment are recorded in
[docs/PHASE4_VALIDATION.md](docs/PHASE4_VALIDATION.md).
The Phase 5 schedule/transport implementation and 64-frame real-cut
simulation are recorded in
[docs/PHASE5_VALIDATION.md](docs/PHASE5_VALIDATION.md).
The Phase 6 per-FPGA split, endpoint/lane agreement checks, generated
transport RTL, and 64-cycle mapped PicoRV32 equivalence result are recorded in
[docs/PHASE6_VALIDATION.md](docs/PHASE6_VALIDATION.md).
The Phase 7A real transport overhead and two-partition OpenPARF placement
results are recorded in
[docs/PHASE7A_VALIDATION.md](docs/PHASE7A_VALIDATION.md).
The Phase 7B structural-netlist export and two fully routed, DRC-clean Vivado
checkpoints are recorded in
[docs/PHASE7B_VALIDATION.md](docs/PHASE7B_VALIDATION.md).
The Phase 7C integrated runtime controller, two-clock timing closure, and
end-to-end PicoRV32 QoR are recorded in
[docs/PHASE7C_VALIDATION.md](docs/PHASE7C_VALIDATION.md).
The Phase 7D cross-phase G0-G9 audit and sealed release manifest are recorded
in [docs/PHASE7D_VALIDATION.md](docs/PHASE7D_VALIDATION.md).

Phase 2 currently uses a conservative physical policy: only the eight `*6LUT`
and eight primary `*FF` BELs in each SLICE are exposed. Paired `*5LUT`,
secondary FF, carry/macro packing, FPGA Interchange physical-netlist patching,
and RapidWright DCP conversion remain explicit follow-on work. The tiny
eight-cell smoke test runs OpenPARF global placement and UltraScale
legalization; its optional ISM detailed-placement pass is disabled because
the upstream implementation assumes a production-scale design.
