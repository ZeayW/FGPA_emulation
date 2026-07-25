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
| `bootstrap` | Create a project-local virtual environment and install `yowasp-yosys` if no project-local Yosys exists. |
| `test` | Run all Python unit tests and byte-compile the source. |
| `synth` | Run a real Yosys process with `synth_xilinx -family xcup` on `counter.v`. |
| `phase1` | Import the synthesized JSON and run the Phase 1 platform/resource checks. |
| `all` | Execute the complete sequence above. |

`yowasp-yosys` is installed under the remote project's `.venv`; no root
permission or global package modification is required. The generated
artifacts are placed under `build/remote/`.

## Configuration

The defaults can be changed without editing the script:

```bash
EMUFLOW_REMOTE_DIR=/some/other/path \
  scripts/remote/proj169-2.sh all
```

Supported overrides are `EMUFLOW_SSH_ALIAS`, `EMUFLOW_INNER_HOST`,
`EMUFLOW_INNER_PORT`, `EMUFLOW_REMOTE_DIR`, and
`EMUFLOW_REMOTE_KNOWN_HOSTS`.

## Current environment observation

At the time this integration was added, the final node reported host
name `proj169`. Python 3.10, Git, CMake, and Ninja were available.
System-level Yosys, Vivado, and OpenPARF were not present in the default
`PATH` or the standard tool directories that were checked. The
project-local Yosys bootstrap handles the Phase 1 synthesis dependency;
Vivado and OpenPARF remain later-phase dependencies.
