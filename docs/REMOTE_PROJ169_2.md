# Running on `proj169-2`

The local SSH alias `proj169-2` first connects to
`gw.cse.cuhk.edu.hk`, then starts a second SSH session to
`ziyiwang21@projgw` on port `2369`. The repository wrapper hides this
two-hop detail:

```bash
scripts/remote/proj169-2.sh probe
scripts/remote/proj169-2.sh all
```

The default remote deployment directory is:

```text
/home/ziyiwang21/work/FGPA_emulation
```

The wrapper uploads only the current committed Git snapshot. It refuses
to synchronize a dirty worktree, records the source commit in
`.emuflow-source-commit`, and leaves local build products behind.

## Commands

| Command | Purpose |
| --- | --- |
| `probe` | Report the final host name and discover Python, Git, Yosys, Vivado, OpenPARF, CMake, and Ninja. |
| `sync` | Stream `git archive HEAD` into the remote deployment directory. |
| `bootstrap` | Use the server OSS CAD Suite Yosys, or install `yowasp-yosys` as a fallback. |
| `tritonpart-bootstrap` | Cache the pinned OpenROAD package locally, atomically upload it through the gateway, verify SHA-256 remotely, and install TritonPart without root. |
| `test` | Run all Python unit tests and byte-compile the source. |
| `synth` | Run a real Yosys process with `synth_xilinx -family xcup` on `counter.v`. |
| `phase1` | Import the synthesized JSON and run the Phase 1 platform/resource checks. |
| `phase2-arch` | Use Vivado to export a compact window of up to 512 real `xcvu3p` SLICE sites and import it as ArchitectureDB. |
| `phase2-arch-large` | Export up to 32,768 SLICE sites for 100k-cell placement; the validated VU3P inventory contains 30,038 sites. |
| `phase2` | Export the locked LUT2/FDRE mapped fixture to Bookshelf and validate a deterministic reference Site/BEL placement. |
| `phase2-vivado` | Apply the checked LOC/BEL constraints, complete placement, route, DRC, and write a DCP. |
| `openparf-sync` | Upload the local upstream OpenPARF source checkout. |
| `openparf-build` | Build and install CPU-only OpenPARF in the server `deepgate` environment. |
| `openparf-run` | Run real OpenPARF global placement/legalization and re-import its `.pl` result. |
| `phase2-vivado-openparf` | Route the re-imported OpenPARF LOC/BEL placement in Vivado and write a routed DCP. |
| `serv-sync`, `serv-l1`, `serv-l1-all` | Synchronize or run the 436-cell SERV L1 physical regression. |
| `picorv32-sync`, `picorv32-l2`, `picorv32-l2-all` | Synchronize or run the 3812-cell PicoRV32 L2 physical regression. |
| `picorv32-x32-synth` | Synthesize the x32 PicoRV32 harness and require at least 100,000 mapped cells. |
| `picorv32-x32-openparf` | Place and legalize the 100k-cell design with OpenPARF. |
| `picorv32-x32-vivado` | Import the 100k-cell placement, route it with Vivado, and require a routed DCP. |
| `picorv32-x32-phase3` | Run deterministic TritonPart plus greedy A/B, validate 100k-cell G4 scale and connected-PicoRV32 legal cuts on two virtual FPGAs. |
| `picorv32-phase4` | Route all connected-PicoRV32 cut nets over BoardDB and independently validate G5. |
| `picorv32-phase5` | Schedule all routed PicoRV32 bit-hops and run Python plus compiled RTL transport simulation. |
| `picorv32-phase6` | Split connected PicoRV32 into per-FPGA netlists, compile transport RTL, and run mapped cycle equivalence. |
| `picorv32-phase7a` | Synthesize transport, stitch both placement graphs, and run OpenPARF independently per FPGA. |
| `picorv32-phase7b` | Emit both structural netlists and route the OpenPARF placements with Vivado. |
| `picorv32-phase7c` | Generate/simulate the runtime contract, reroute both partitions with DUT/fabric constraints, and aggregate QoR. |
| `picorv32-phase7c-finalize` | Reopen existing runtime-constrained DCPs, gate all timing groups, and regenerate QoR. |
| `picorv32-phase7c-all` | Rebuild Phase 6 and 7A before the complete Phase 7C run. |
| `picorv32-phase7d` | Cross-check G0-G9, rehash sources and 18 critical artifacts, and seal a release manifest. |
| `koios-sync`, `koios-dla-small-synth`, `koios-dla-medium-synth` | Synchronize or run bounded Koios DLA synthesis experiments. |
| `nvdla-sync`, `nvdla-screen` | Synchronize the pinned official NVDLA nvdlav1 source and run the connected 3.12M-cell Vivado scale gate. |
| `nvdla-partition-a-synth` | Synthesize the connected NVDLA CACC partition, require zero black boxes, soft-map it to LUT/FF EmuIR, and enforce a 300k-cell gate. |
| `nvdla-arch-vu9p-full` | Export and import the complete 147,780-SLICE VU9P architecture database. |
| `openparf-build-sparse` | Build the experimental isolated greedy-first legalizer used by a recorded negative-control run. |
| `phase2-all` | Run the Phase 1 plus reference Phase 2 validation sequence. |
| `all` | Execute the complete sequence above. |

The default Yosys executable is
`/data/zhpei/oss-cad-suite/bin/yosys` (Yosys `0.33+103`, with `xcup`
support). If that executable is unavailable, `yowasp-yosys` can be
installed under the remote project's `.venv`; no root permission or
global package modification is required. The generated artifacts are
placed under `build/remote/`.

## Configuration

The defaults can be changed without editing the script:

```bash
EMUFLOW_REMOTE_DIR=/some/other/path \
  scripts/remote/proj169-2.sh all
```

Supported overrides are `EMUFLOW_SSH_ALIAS`, `EMUFLOW_INNER_HOST`,
`EMUFLOW_INNER_PORT`, `EMUFLOW_REMOTE_DIR`, and
`EMUFLOW_REMOTE_KNOWN_HOSTS`. `EMUFLOW_VIVADO_ROOT` defaults to
`/data2/vivado/2025.2/Vivado`. `EMUFLOW_CONTROL_PATH` can select an
existing SSH ControlMaster socket. When it is unset, the wrapper checks
`~/.ssh/control/` and reuses a live master before opening a new gateway
connection. `EMUFLOW_YOSYS` overrides the server Yosys executable.
`EMUFLOW_OPENROAD_ROOT` selects the user-space OpenROAD installation root and
`EMUFLOW_OPENROAD` overrides the executable wrapper.

## Current environment observation

At the time this integration was added, the final node reported host
name `proj169`. Python 3.10, Git, CMake, and Ninja were available.
System-level Yosys, Vivado, and OpenPARF are not present in the
non-interactive SSH session's default `PATH`. The wrapper uses OSS CAD Suite
Yosys at `/data/zhpei/oss-cad-suite/bin/yosys` and Vivado 2025.2 at
`/data2/vivado/2025.2/Vivado`. A CPU-only OpenPARF build is installed under
`/home/ziyiwang21/work/tools/OpenPARF-install` using the `deepgate` Conda
Python. The versioned compatibility patches under
`scripts/openparf/patches/` adapt the upstream source to that environment.

The validated real-placement sequence is:

```bash
scripts/remote/proj169-2.sh phase2-arch
scripts/remote/proj169-2.sh phase2
scripts/remote/proj169-2.sh openparf-run
scripts/remote/proj169-2.sh phase2-vivado-openparf
```

The run produces a legal eight-cell OpenPARF placement and
`build/remote/phase2/vivado-openparf/routed.dcp`. The `phase2` command by
itself never labels its deterministic fallback as OpenPARF. Phase 2 uses the
checked-in mapped fixture: the server Yosys currently emits `CARRY4` for the
counter, and legal conversion to the UltraScale+ `CARRY8` macro is a
site-packing task rather than a placement conversion.

The real-RTL scale regressions are:

```bash
scripts/remote/proj169-2.sh serv-l1-all
scripts/remote/proj169-2.sh picorv32-l2-all
```

Their routed checkpoints are written below `build/remote/benchmarks/`.
PicoRV32 uses 461 of the 483 exported sites. See
`docs/PICORV32_L2_VALIDATION.md` for its resource, routing, timing, and
control-set repair results.

The 100k-cell physical scale regression is:

```bash
scripts/remote/proj169-2.sh phase2-arch-large
scripts/remote/proj169-2.sh picorv32-x32-synth
scripts/remote/proj169-2.sh picorv32-x32-openparf
scripts/remote/proj169-2.sh picorv32-x32-vivado
```

It produces a legal 121,984-cell OpenPARF placement and a fully routed,
DRC-clean checkpoint below
`build/remote/benchmarks/picorv32-x32-l5/vivado/`. See
`docs/PICORV32_X32_100K_VALIDATION.md` for exact runtime, congestion, timing,
and semantic limits.

Run the completed Phase 3 G4 regression:

```bash
scripts/remote/proj169-2.sh tritonpart-bootstrap
scripts/remote/proj169-2.sh picorv32-x32-phase3
```

The bootstrap installs OpenROAD `v2.0-17598-ga008522d8` below
`/home/ziyiwang21/work/tools/` from a package pinned by SHA-256. The
regression performs two fixed-seed TritonPart runs per design, compares
complete assignment SHA-256 values, runs a greedy A/B baseline, and invokes
the independent checker. The x32 run validates 121,984-cell scale and exact
60,992/60,992 balance; connected PicoRV32 validates 140 real register-output
cut nets and zero illegal cuts. See `docs/PHASE3_VALIDATION.md`.

Run the completed Phase 4 G5 regression:

```bash
scripts/remote/proj169-2.sh picorv32-phase4
```

It routes all 140 connected-PicoRV32 cut demands, independently validates
reachability, acyclicity, direction, latency, and link capacity, then repeats
the run and compares complete route-artifact SHA-256 values. See
`docs/PHASE4_VALIDATION.md`.

Run the Phase 5 schedule/transport regression:

```bash
scripts/remote/proj169-2.sh picorv32-phase5
```

It independently validates all 140 lane/slot assignments, repeats and hashes
the schedule, simulates 64 frames in Python, compiles the generic TDM link and
frame-barrier RTL, and runs a generated self-checking SystemVerilog testbench
with Icarus. See `docs/PHASE5_VALIDATION.md`.

Run the Phase 6 board-independent netlist/lane regression:

```bash
scripts/remote/proj169-2.sh picorv32-phase6
```

It re-imports the real mapped PicoRV32 JSON with constant primitive pins,
creates both per-FPGA netlists and paired transport endpoints, independently
reconstructs the lane bindings, compiles both generated transport modules,
repeats and hashes all principal artifacts, and proves 64 mapped virtual DUT
cycles. See `docs/PHASE6_VALIDATION.md`.

Run the Phase 7A per-FPGA placement regression:

```bash
scripts/remote/proj169-2.sh picorv32-phase7a
```

It maps both generated transport modules with Yosys, stitches the resulting
LUT/FF graphs into the corresponding partitions, runs OpenPARF twice, and
checks every resulting Site/BEL assignment. See
`docs/PHASE7A_VALIDATION.md`.

Run the Phase 7B routed-DCP regression:

```bash
scripts/remote/proj169-2.sh picorv32-phase7b
```

It emits complete structural Xilinx primitive Verilog for both merged
partitions, applies the OpenPARF placement tables, routes both designs, and
requires exact cell coverage, zero unrouted nets, and routed DCPs. See
`docs/PHASE7B_VALIDATION.md`.

Run the Phase 7C virtual-runtime regression:

```bash
scripts/remote/proj169-2.sh picorv32-phase7c-all
```

It integrates a mapped frame controller into both transports, simulates 64
frames with an intentional barrier stall, routes 4,223 total cells, and
independently checks 4 ns fabric timing, 128 ns nominal DUT timing, the 100 ns
fabric-to-DUT stable-data window, zero unrouted nets, and zero DRC violations.
The worst routed WNS is +2.642 ns. See `docs/PHASE7C_VALIDATION.md`.

Seal the board-independent run:

```bash
scripts/remote/proj169-2.sh picorv32-phase7d
```

It runs the release audit twice, requires byte-identical manifests, and checks
all G0-G9 gates. The validated manifest SHA-256 is
`15e226ab36bc7995dbddf26103204957a6905c4f4b05e9ab8649e9272b5fe7c9`.
See `docs/PHASE7D_VALIDATION.md`.

Run the genuine million-cell frontend screen:

```bash
scripts/remote/proj169-2.sh nvdla-sync
scripts/remote/proj169-2.sh nvdla-screen
```

The validated `NV_nvdla` synthesis produced 3,123,117 hierarchical cells,
1,825,473 LUTs, 915,739 FFs, and 459 DSP blocks in 54:47.72. The current
Yosys JSON bridge is intentionally not part of this command: a measured
attempt reached 118 GB RSS without producing JSON and was stopped before host
memory exhaustion. See `docs/NVDLA_NVDLAV1_VALIDATION.md`.

Run the connected hundreds-of-thousands-cell CACC frontend and export its
full physical target:

```bash
scripts/remote/proj169-2.sh nvdla-partition-a-synth
scripts/remote/proj169-2.sh nvdla-arch-vu9p-full
```

The gated-clock-converted design contains 731,313 EmuIR LUT/FF cells
(334,522 LUTs and 396,791 FFs), one legal FF clock net, no fabric-routed
clock net, and zero black boxes. The downstream four-FPGA TritonPart,
system-routing, TDM, virtual-pin, split/equivalence, and physical results are
recorded in `docs/NVDLA_PARTITION_A_FULL_FLOW.md`.

For an existing complete Phase 6 experiment root, reproduce the physical
stages directly on `proj169-2`:

```bash
scripts/remote/nvdla_partition_a_phase7.sh phase7a ROOT
scripts/remote/nvdla_partition_a_phase7.sh phase7b ROOT
scripts/remote/nvdla_partition_a_phase7.sh phase7c-finalize ROOT
scripts/remote/nvdla_partition_a_phase7.sh phase7d ROOT
```

The large partition defaults to one fixed LUT on each deterministic 1/64
sample of OpenPARF-used SLICE sites. Override
`EMUFLOW_MAIN_ANCHOR_MODULUS`, `EMUFLOW_MAIN_PLACE_DIRECTIVE`, or
`EMUFLOW_MAIN_ROUTE_DIRECTIVE` to run explicitly labeled placement A/B
experiments.

The validated sparse-anchor run preserves 870 OpenPARF anchors and all
731,331 mapped main-partition cell identities. Its 679,250 routable nets
converge to zero routing errors with +1.435 ns WNS. Vivado inserts one
explicitly audited `BUFGCE`, so the four-DCP Phase 7C result contains 731,387
mapped cells and 731,388 physical cells, with zero unrouted nets and zero DRC
violations. `phase7c-finalize` checks every mapped identity against the saved
pre-placement inventory and rejects any tool-added cell outside the `BUFG*`
infrastructure whitelist.

The validated `phase7d` run rehashes 376 pinned source dependencies and 26
release artifacts, distinguishes 3 routed demands from 4 multi-hop scheduled
bit-hops, and requires two byte-identical G0-G9 manifests. The validated
manifest SHA-256 is
`cc965f733830ec6a32c5357a516b36a96b160c8f85ac23ad26714507a713ef0a`.
