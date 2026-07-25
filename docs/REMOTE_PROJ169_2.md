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
| `phase2-arch` | Use Vivado to export 64 real `xcvu3p` SLICE sites and import them as ArchitectureDB. |
| `phase2` | Export the locked LUT2/FDRE mapped fixture to Bookshelf and validate a deterministic reference Site/BEL placement. |
| `phase2-vivado` | Apply the checked LOC/BEL constraints, complete placement, route, DRC, and write a DCP. |
| `openparf-sync` | Upload the local upstream OpenPARF source checkout. |
| `openparf-build` | Build and install CPU-only OpenPARF in the server `deepgate` environment. |
| `openparf-run` | Run real OpenPARF global placement/legalization and re-import its `.pl` result. |
| `phase2-vivado-openparf` | Route the re-imported OpenPARF LOC/BEL placement in Vivado and write a routed DCP. |
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
