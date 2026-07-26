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
| `picorv32-x32-phase3` | Validate 100k-cell G4 scale and connected-PicoRV32 legal cut extraction on two virtual FPGAs. |
| `picorv32-phase4` | Route all connected-PicoRV32 cut nets over BoardDB and independently validate G5. |
| `picorv32-phase5` | Schedule all routed PicoRV32 bit-hops and run Python plus compiled RTL transport simulation. |
| `koios-sync`, `koios-dla-small-synth`, `koios-dla-medium-synth` | Synchronize or run bounded Koios DLA synthesis experiments. |
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
scripts/remote/proj169-2.sh picorv32-x32-phase3
```

This performs two fixed-seed runs for each design, compares complete
assignment SHA-256 values, and invokes the independent partition checker. The
x32 run validates 121,984-cell scale and exact 60,992/60,992 balance; the
connected PicoRV32 run validates 140 real register-output cut nets and zero
illegal cuts. See `docs/PHASE3_VALIDATION.md`.

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
