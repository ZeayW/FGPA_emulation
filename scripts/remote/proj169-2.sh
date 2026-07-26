#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"

SSH_ALIAS="${EMUFLOW_SSH_ALIAS:-proj169-2}"
INNER_HOST="${EMUFLOW_INNER_HOST:-ziyiwang21@projgw}"
INNER_PORT="${EMUFLOW_INNER_PORT:-2369}"
REMOTE_DIR="${EMUFLOW_REMOTE_DIR:-/home/ziyiwang21/work/FGPA_emulation}"
KNOWN_HOSTS="${EMUFLOW_REMOTE_KNOWN_HOSTS:-/tmp/emuflow_proj169_known_hosts}"
VIVADO_ROOT="${EMUFLOW_VIVADO_ROOT:-/data2/vivado/2025.2/Vivado}"
YOSYS_PATH="${EMUFLOW_YOSYS:-/data/zhpei/oss-cad-suite/bin/yosys}"
CONTROL_PATH="${EMUFLOW_CONTROL_PATH:-}"
OPENPARF_SOURCE="${EMUFLOW_OPENPARF_SOURCE:-$REPO_ROOT/../OpenPARF-src}"
OPENPARF_REMOTE_ROOT="${EMUFLOW_OPENPARF_REMOTE_ROOT:-/home/ziyiwang21/work/tools}"

usage() {
  cat <<'EOF'
Usage: scripts/remote/proj169-2.sh COMMAND

Commands:
  probe      Inspect the remote host and available FPGA tools.
  sync       Upload the current committed Git snapshot.
  bootstrap  Select server Yosys or install a user-space fallback.
  test       Run the Python unit tests on the remote host.
  synth      Synthesize examples/rtl/counter.v with a real Yosys process.
  phase1     Run Phase 1 from the remotely synthesized Yosys JSON.
  phase2-arch
             Export a real xcvu3p Site/BEL inventory with Vivado.
  phase2-arch-large
             Export up to 32,768 xcvu3p SLICE sites for 100k-cell runs.
  phase2     Export OpenPARF input and create a checked reference placement.
  phase2-vivado
             Apply the checked placement and route it with Vivado.
  phase2-vivado-openparf
             Apply the re-imported OpenPARF placement and route it with Vivado.
  serv-sync  Upload the pinned SERV source checkout.
  serv-l1    Run the SERV logic-only RTL-to-routed-DCP validation.
  serv-l1-all
             Sync project and SERV, test, export ArchitectureDB, and run SERV L1.
  picorv32-sync
             Upload the pinned PicoRV32 source checkout.
  picorv32-l2
             Run the PicoRV32 logic-only RTL-to-routed-DCP validation.
  picorv32-l2-all
             Sync project and PicoRV32, test, export ArchitectureDB, and run L2.
  picorv32-x32-synth
             Synthesize 32 PicoRV32 cores and require 100,000 mapped cells.
  picorv32-x32-openparf
             Place the synthesized 32-core design with OpenPARF.
  picorv32-x32-vivado
             Route the 32-core OpenPARF placement with Vivado.
  picorv32-x32-phase3
             Validate G4 scale on x32 and legal cuts on connected PicoRV32.
  picorv32-phase4
             Route connected PicoRV32 cut nets over the virtual BoardDB.
  picorv32-phase5
             Schedule and simulate connected PicoRV32 TDM transport.
  picorv32-phase6
             Split connected PicoRV32, compile endpoints, and prove cycles.
  picorv32-phase7a
             Synthesize transport and place both merged FPGA partitions.
  picorv32-phase7b
             Emit, place, and route both FPGA partitions with Vivado.
  koios-sync Upload the pinned Koios DLA small/medium sources.
  koios-dla-small-synth
             Synthesize DLA-small and require at least 100,000 mapped cells.
  koios-dla-medium-synth
             Synthesize DLA-medium and require at least 100,000 mapped cells.
  openparf-sync
             Upload an existing local OpenPARF source checkout.
  openparf-build
             Build a CPU OpenPARF in the server deepgate environment.
  openparf-run
             Run OpenPARF and re-import its placement through Phase 2.
  phase2-all Run sync, Phase 1, ArchitectureDB, Phase 2, and Vivado validation.
  all        Run sync, bootstrap, test, synth, and phase1.

Environment overrides:
  EMUFLOW_SSH_ALIAS
  EMUFLOW_INNER_HOST
  EMUFLOW_INNER_PORT
  EMUFLOW_REMOTE_DIR
  EMUFLOW_REMOTE_KNOWN_HOSTS
  EMUFLOW_VIVADO_ROOT
  EMUFLOW_YOSYS
  EMUFLOW_CONTROL_PATH
  EMUFLOW_OPENPARF_SOURCE
  EMUFLOW_OPENPARF_REMOTE_ROOT
EOF
}

shell_quote() {
  printf '%q' "$1"
}

resolve_control_path() {
  local candidate

  if [ -n "$CONTROL_PATH" ]; then
    return
  fi
  for candidate in "$HOME"/.ssh/control/*; do
    if [ -S "$candidate" ] &&
      ssh -S "$candidate" -O check "$SSH_ALIAS" >/dev/null 2>&1; then
      CONTROL_PATH="$candidate"
      return
    fi
  done
}

gateway_ssh() {
  local -a options=(
    -A
    -o RemoteCommand=none
    -o RequestTTY=no
    -o BatchMode=yes
    -o ConnectTimeout=20
  )

  resolve_control_path
  if [ -n "$CONTROL_PATH" ]; then
    options+=(-S "$CONTROL_PATH")
  fi
  ssh "${options[@]}" "$SSH_ALIAS" "$@"
}

inner_ssh_command() {
  local command="$1"
  printf \
    'ssh -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=no -o UserKnownHostsFile=%s -p %s %s %s' \
    "$(shell_quote "$KNOWN_HOSTS")" \
    "$(shell_quote "$INNER_PORT")" \
    "$(shell_quote "$INNER_HOST")" \
    "$(shell_quote "$command")"
}

remote_script() {
  local remote_dir_quoted
  local vivado_root_quoted
  local yosys_path_quoted
  local command
  remote_dir_quoted="$(shell_quote "$REMOTE_DIR")"
  vivado_root_quoted="$(shell_quote "$VIVADO_ROOT")"
  yosys_path_quoted="$(shell_quote "$YOSYS_PATH")"
  command="$(inner_ssh_command \
    "/bin/bash --noprofile --norc -s -- $remote_dir_quoted $vivado_root_quoted $yosys_path_quoted")"
  gateway_ssh "$command"
}

probe() {
  remote_script <<'REMOTE'
set -eu
remote_dir="$1"
vivado_root="$2"
yosys_path="$3"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

printf 'host=%s\n' "$(hostname)"
printf 'user=%s\n' "$(id -un)"
printf 'remote_dir=%s\n' "$remote_dir"
for tool in python3 git openparf openparf.py cmake ninja; do
  if command -v "$tool" >/dev/null 2>&1; then
    printf '%s=%s\n' "$tool" "$(command -v "$tool")"
  else
    printf '%s=MISSING\n' "$tool"
  fi
done
if command -v yosys >/dev/null 2>&1; then
  yosys_bin="$(command -v yosys)"
elif [ -x "$yosys_path" ]; then
  yosys_bin="$yosys_path"
else
  yosys_bin=""
fi
if [ -n "$yosys_bin" ]; then
  printf 'yosys=%s\n' "$yosys_bin"
  "$yosys_bin" -V
else
  printf 'yosys=NOT_FOUND_AT_%s\n' "$yosys_path"
fi
if command -v vivado >/dev/null 2>&1; then
  vivado_bin="$(command -v vivado)"
elif [ -x "$vivado_root/bin/vivado" ]; then
  vivado_bin="$vivado_root/bin/vivado"
else
  vivado_bin=""
fi
if [ -n "$vivado_bin" ]; then
  printf 'vivado=%s\n' "$vivado_bin"
  "$vivado_bin" -version | head -4
else
  printf 'vivado=NOT_FOUND_AT_%s\n' "$vivado_root"
fi
if [ -x "$remote_dir/.venv/bin/yowasp-yosys" ]; then
  printf 'project_yosys=%s\n' "$remote_dir/.venv/bin/yowasp-yosys"
  "$remote_dir/.venv/bin/yowasp-yosys" -V
fi
REMOTE
}

sync_project() {
  local commit
  local remote_dir_quoted
  local unpack_command

  if [ -n "$(git status --porcelain)" ]; then
    echo "error: sync requires a clean Git worktree" >&2
    return 1
  fi
  commit="$(git rev-parse HEAD)"
  remote_dir_quoted="$(shell_quote "$REMOTE_DIR")"
  unpack_command="$(inner_ssh_command \
    "mkdir -p $remote_dir_quoted && tar -xf - -C $remote_dir_quoted")"

  git archive --format=tar HEAD | gateway_ssh "$unpack_command"
  remote_script <<REMOTE
set -eu
remote_dir="\$1"
printf '%s\n' "$(shell_quote "$commit")" > "\$remote_dir/.emuflow-source-commit"
printf 'synced_commit=%s\n' "\$(cat "\$remote_dir/.emuflow-source-commit")"
REMOTE
}

bootstrap() {
  remote_script <<'REMOTE'
set -eu
remote_dir="$1"
yosys_path="$3"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd "$remote_dir"

if [ -x "$yosys_path" ]; then
  "$yosys_path" -V
  exit 0
fi
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
if [ ! -x .venv/bin/yowasp-yosys ]; then
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install yowasp-yosys
fi
.venv/bin/yowasp-yosys -V
REMOTE
}

test_remote() {
  remote_script <<'REMOTE'
set -eu
remote_dir="$1"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd "$remote_dir"
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
REMOTE
}

synth_remote() {
  remote_script <<'REMOTE'
set -eu
remote_dir="$1"
yosys_path="$3"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd "$remote_dir"
mkdir -p build/remote
if [ -x "$yosys_path" ]; then
  yosys_bin="$yosys_path"
elif [ -x .venv/bin/yowasp-yosys ]; then
  yosys_bin=.venv/bin/yowasp-yosys
elif command -v yosys >/dev/null 2>&1; then
  yosys_bin="$(command -v yosys)"
else
  echo "error: no usable Yosys executable found" >&2
  exit 1
fi
PYTHONPATH=src python3 -m emuflow synth-yosys \
  examples/rtl/counter.v \
  --top counter \
  --family xcup \
  --yosys "$yosys_bin" \
  --output build/remote/counter.json \
  --log build/remote/counter-yosys.log
test -s build/remote/counter.json
REMOTE
}

phase1_remote() {
  remote_script <<'REMOTE'
set -eu
remote_dir="$1"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd "$remote_dir"
PYTHONPATH=src python3 -m emuflow phase1 \
  --yosys-json build/remote/counter.json \
  --top counter \
  --clock clk \
  --platform platforms/virtual/xcvu3p_2fpga_p2p.json \
  --out build/remote/phase1
REMOTE
}

phase2_arch_remote() {
  remote_script <<'REMOTE'
set -eu
remote_dir="$1"
vivado_root="$2"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd "$remote_dir"
mkdir -p build/remote/phase2
"$vivado_root/bin/vivado" -mode batch -nojournal -nolog \
  -source scripts/vivado/export_architecture.tcl \
  -tclargs xcvu3p-ffvc1517-2-e \
  build/remote/phase2/xcvu3p.sites.tsv 512 \
  > build/remote/phase2/vivado-arch.log 2>&1
PYTHONPATH=src python3 -m emuflow arch import-vivado-tsv \
  build/remote/phase2/xcvu3p.sites.tsv \
  --output build/remote/phase2/xcvu3p.arch.json
REMOTE
}

phase2_arch_large_remote() {
  remote_script <<'REMOTE'
set -eu
remote_dir="$1"
vivado_root="$2"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd "$remote_dir"
mkdir -p build/remote/phase2-large
"$vivado_root/bin/vivado" -mode batch -nojournal -nolog \
  -source scripts/vivado/export_architecture.tcl \
  -tclargs xcvu3p-ffvc1517-2-e \
  build/remote/phase2-large/xcvu3p.sites.tsv 32768 \
  > build/remote/phase2-large/vivado-arch.log 2>&1
PYTHONPATH=src python3 -m emuflow arch import-vivado-tsv \
  build/remote/phase2-large/xcvu3p.sites.tsv \
  --output build/remote/phase2-large/xcvu3p.arch.json
du -sh build/remote/phase2-large
REMOTE
}

phase2_remote() {
  remote_script <<'REMOTE'
set -eu
remote_dir="$1"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd "$remote_dir"
test -s build/remote/phase2/xcvu3p.arch.json
# Phase 2 deliberately starts from a mapped primitive fixture. The server's
# bootstrap Yosys emits CARRY4 for this RTL; converting that cell into a legal
# UltraScale+ CARRY8 site macro belongs to the later site-packing milestone.
PYTHONPATH=src python3 -m emuflow import-yosys \
  examples/yosys/counter.json \
  --top counter \
  --clock clk \
  --output build/remote/phase2/counter.emuir.json
PYTHONPATH=src python3 -m emuflow phase2 \
  --ir build/remote/phase2/counter.emuir.json \
  --arch build/remote/phase2/xcvu3p.arch.json \
  --out build/remote/phase2/run
REMOTE
}

phase2_vivado_remote() {
  remote_script <<'REMOTE'
set -eu
remote_dir="$1"
vivado_root="$2"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd "$remote_dir"
test -s build/remote/phase2/run/placement.xdc
"$vivado_root/bin/vivado" -mode batch -nojournal -nolog \
  -source scripts/vivado/validate_phase2.tcl \
  -tclargs xcvu3p-ffvc1517-2-e \
  examples/rtl/phase2_primitives.v \
  build/remote/phase2/run/placement.xdc \
  build/remote/phase2/vivado \
  > build/remote/phase2/vivado-validation.log 2>&1
grep 'EMUFLOW_PHASE2_VIVADO status=pass' \
  build/remote/phase2/vivado-validation.log
test -s build/remote/phase2/vivado/routed.dcp
REMOTE
}

phase2_vivado_openparf_remote() {
  remote_script <<'REMOTE'
set -eu
remote_dir="$1"
vivado_root="$2"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd "$remote_dir"
test -s build/remote/phase2/run-openparf/placement.xdc
"$vivado_root/bin/vivado" -mode batch -nojournal -nolog \
  -source scripts/vivado/validate_phase2.tcl \
  -tclargs xcvu3p-ffvc1517-2-e \
  examples/rtl/phase2_primitives.v \
  build/remote/phase2/run-openparf/placement.xdc \
  build/remote/phase2/vivado-openparf \
  > build/remote/phase2/vivado-openparf-validation.log 2>&1
grep 'EMUFLOW_PHASE2_VIVADO status=pass' \
  build/remote/phase2/vivado-openparf-validation.log
test -s build/remote/phase2/vivado-openparf/routed.dcp
REMOTE
}

sync_openparf() {
  local remote_root_quoted
  local unpack_command

  if [ ! -f "$OPENPARF_SOURCE/CMakeLists.txt" ]; then
    echo "error: OpenPARF source not found at $OPENPARF_SOURCE" >&2
    return 1
  fi
  remote_root_quoted="$(shell_quote "$OPENPARF_REMOTE_ROOT")"
  unpack_command="$(inner_ssh_command \
    "mkdir -p $remote_root_quoted/OpenPARF-src && tar -xzf - -C $remote_root_quoted/OpenPARF-src")"
  COPYFILE_DISABLE=1 tar -czf - \
    --exclude=.git \
    --exclude='*/.git' \
    --exclude=build \
    -C "$OPENPARF_SOURCE" . | gateway_ssh "$unpack_command"
}

sync_serv_source() {
  local source="$REPO_ROOT/third_party/rtl/serv"
  local destination_quoted
  local unpack_command

  python3 "$REPO_ROOT/scripts/benchmarks/fetch.py" fetch serv
  if [ ! -f "$source/rtl/serv_synth_wrapper.v" ]; then
    echo "error: SERV source is incomplete at $source" >&2
    return 1
  fi
  destination_quoted="$(shell_quote "$REMOTE_DIR/third_party/rtl/serv")"
  unpack_command="$(inner_ssh_command \
    "mkdir -p $destination_quoted && tar -xf - -C $destination_quoted")"
  COPYFILE_DISABLE=1 tar -cf - --exclude=.git -C "$source" . |
    gateway_ssh "$unpack_command"
}

sync_picorv32_source() {
  local source="$REPO_ROOT/third_party/rtl/picorv32"
  local destination_quoted
  local unpack_command

  python3 "$REPO_ROOT/scripts/benchmarks/fetch.py" fetch picorv32
  if [ ! -f "$source/picorv32.v" ]; then
    echo "error: PicoRV32 source is incomplete at $source" >&2
    return 1
  fi
  destination_quoted="$(shell_quote "$REMOTE_DIR/third_party/rtl/picorv32")"
  unpack_command="$(inner_ssh_command \
    "mkdir -p $destination_quoted && tar -xf - -C $destination_quoted")"
  COPYFILE_DISABLE=1 tar -cf - --exclude=.git -C "$source" . |
    gateway_ssh "$unpack_command"
}

sync_koios_source() {
  local source="$REPO_ROOT/third_party/rtl/koios"
  local benchmark_source="$source/vtr_flow/benchmarks/verilog/koios"
  local destination_quoted
  local unpack_command

  python3 "$REPO_ROOT/scripts/benchmarks/fetch.py" fetch koios
  if [ ! -f "$benchmark_source/dla_like.small.v" ] ||
    [ ! -f "$benchmark_source/dla_like.medium.v" ]; then
    echo "error: Koios DLA sources are incomplete at $benchmark_source" >&2
    return 1
  fi
  destination_quoted="$(shell_quote \
    "$REMOTE_DIR/third_party/rtl/koios/vtr_flow/benchmarks/verilog/koios")"
  unpack_command="$(inner_ssh_command \
    "mkdir -p $destination_quoted && tar -xf - -C $destination_quoted")"
  COPYFILE_DISABLE=1 tar -cf - \
    -C "$benchmark_source" dla_like.small.v dla_like.medium.v README.md |
    gateway_ssh "$unpack_command"
}

serv_l1_remote() {
  local remote_root_quoted
  remote_root_quoted="$(shell_quote "$OPENPARF_REMOTE_ROOT")"
  remote_script <<REMOTE
set -eu
remote_dir="\$1"
vivado_root="\$2"
yosys_path="\$3"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
openparf_root=$remote_root_quoted
openparf_python=/home/ziyiwang21/anaconda3/envs/deepgate/bin/python
cd "\$remote_dir"

test -x "\$yosys_path"
test -x "\$openparf_python"
test -f "\$openparf_root/OpenPARF-install/openparf.py"
test -s build/remote/phase2/xcvu3p.arch.json
test -f third_party/rtl/serv/rtl/serv_synth_wrapper.v

PYTHONPATH=src python3 -m emuflow benchmark \
  benchmarks/runs/serv_l1.json \
  --source-root third_party/rtl/serv \
  --out build/remote/benchmarks/serv-l1 \
  --yosys "\$yosys_path"

PYTHONPATH=src python3 -m emuflow phase2 \
  --ir build/remote/benchmarks/serv-l1/phase1/design.emuir.json \
  --arch build/remote/phase2/xcvu3p.arch.json \
  --out build/remote/benchmarks/serv-l1/phase2-reference

export CUDA_VISIBLE_DEVICES=""
export PYTHONPATH="\$remote_dir/scripts/openparf/shims:\$openparf_root/OpenPARF-install"
"\$openparf_python" "\$openparf_root/OpenPARF-install/openparf.py" \
  --config build/remote/benchmarks/serv-l1/phase2-reference/openparf/openparf.json \
  --log build/remote/benchmarks/serv-l1/openparf.log

result=build/remote/benchmarks/serv-l1/phase2-reference/openparf/results/serv_synth_wrapper.pl
test -s "\$result"
PYTHONPATH=src python3 -m emuflow phase2 \
  --ir build/remote/benchmarks/serv-l1/phase1/design.emuir.json \
  --arch build/remote/phase2/xcvu3p.arch.json \
  --openparf-result "\$result" \
  --out build/remote/benchmarks/serv-l1/phase2-openparf

"\$vivado_root/bin/vivado" -mode batch -nojournal -nolog \
  -source scripts/vivado/validate_mapped.tcl \
  -tclargs xcvu3p-ffvc1517-2-e \
  build/remote/benchmarks/serv-l1/synthesis/mapped.v \
  serv_synth_wrapper \
  build/remote/benchmarks/serv-l1/phase2-openparf/placement.xdc \
  build/remote/benchmarks/serv-l1/vivado \
  436 clk 10.0 \
  > build/remote/benchmarks/serv-l1/vivado-validation.log 2>&1
grep 'EMUFLOW_MAPPED_VIVADO status=pass' \
  build/remote/benchmarks/serv-l1/vivado-validation.log
test -s build/remote/benchmarks/serv-l1/vivado/routed.dcp
REMOTE
}

picorv32_l2_remote() {
  local remote_root_quoted
  remote_root_quoted="$(shell_quote "$OPENPARF_REMOTE_ROOT")"
  remote_script <<REMOTE
set -eu
remote_dir="\$1"
vivado_root="\$2"
yosys_path="\$3"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
openparf_root=$remote_root_quoted
openparf_python=/home/ziyiwang21/anaconda3/envs/deepgate/bin/python
cd "\$remote_dir"

test -x "\$yosys_path"
test -x "\$openparf_python"
test -f "\$openparf_root/OpenPARF-install/openparf.py"
test -s build/remote/phase2/xcvu3p.arch.json
test -f third_party/rtl/picorv32/picorv32.v

PYTHONPATH=src python3 -m emuflow benchmark \
  benchmarks/runs/picorv32_l2.json \
  --source-root third_party/rtl/picorv32 \
  --out build/remote/benchmarks/picorv32-l2 \
  --yosys "\$yosys_path"

PYTHONPATH=src python3 -m emuflow phase2 \
  --ir build/remote/benchmarks/picorv32-l2/phase1/design.emuir.json \
  --arch build/remote/phase2/xcvu3p.arch.json \
  --out build/remote/benchmarks/picorv32-l2/phase2-reference

export CUDA_VISIBLE_DEVICES=""
export PYTHONPATH="\$remote_dir/scripts/openparf/shims:\$openparf_root/OpenPARF-install"
"\$openparf_python" "\$openparf_root/OpenPARF-install/openparf.py" \
  --config build/remote/benchmarks/picorv32-l2/phase2-reference/openparf/openparf.json \
  --log build/remote/benchmarks/picorv32-l2/openparf.log

result=build/remote/benchmarks/picorv32-l2/phase2-reference/openparf/results/picorv32.pl
test -s "\$result"
PYTHONPATH=src python3 -m emuflow phase2 \
  --ir build/remote/benchmarks/picorv32-l2/phase1/design.emuir.json \
  --arch build/remote/phase2/xcvu3p.arch.json \
  --openparf-result "\$result" \
  --out build/remote/benchmarks/picorv32-l2/phase2-openparf

expected_cells=\$(python3 -c \
  'import json,sys; print(len(json.load(open(sys.argv[1]))["instances"]))' \
  build/remote/benchmarks/picorv32-l2/phase1/design.emuir.json)
"\$vivado_root/bin/vivado" -mode batch -nojournal -nolog \
  -source scripts/vivado/validate_mapped.tcl \
  -tclargs xcvu3p-ffvc1517-2-e \
  build/remote/benchmarks/picorv32-l2/synthesis/mapped.v \
  picorv32 \
  build/remote/benchmarks/picorv32-l2/phase2-openparf/placement.xdc \
  build/remote/benchmarks/picorv32-l2/vivado \
  "\$expected_cells" clk 10.0 \
  > build/remote/benchmarks/picorv32-l2/vivado-validation.log 2>&1
grep 'EMUFLOW_MAPPED_VIVADO status=pass' \
  build/remote/benchmarks/picorv32-l2/vivado-validation.log
test -s build/remote/benchmarks/picorv32-l2/vivado/routed.dcp
REMOTE
}

picorv32_x32_synth_remote() {
  remote_script <<'REMOTE'
set -eu
remote_dir="$1"
yosys_path="$3"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd "$remote_dir"

test -x "$yosys_path"
test -f third_party/rtl/picorv32/picorv32.v
test -f benchmarks/rtl/picorv32_x32_top.v
rm -rf build/remote/benchmarks/picorv32-x32-l5

PYTHONPATH=src python3 -m emuflow benchmark \
  benchmarks/runs/picorv32_x32_l5.json \
  --source-root . \
  --out build/remote/benchmarks/picorv32-x32-l5 \
  --yosys "$yosys_path"

python3 - <<'PY'
import json
from pathlib import Path

root = Path("build/remote/benchmarks/picorv32-x32-l5")
design = json.loads(
    (root / "phase1/design.emuir.json").read_text(encoding="utf-8")
)
counts = {}
for instance in design["instances"]:
    cell_type = instance["type"]
    counts[cell_type] = counts.get(cell_type, 0) + 1
total = len(design["instances"])
print(
    "EMUFLOW_PICORV32_X32_SYNTH "
    f"status={'pass' if total >= 100000 else 'fail'} "
    f"cells={total} types={json.dumps(counts, sort_keys=True)}"
)
if total < 100000:
    raise SystemExit(
        f"mapped design has {total} cells; expected at least 100000"
    )
PY

du -sh \
  build/remote/benchmarks/picorv32-x32-l5 \
  build/remote/benchmarks/picorv32-x32-l5/synthesis/*
REMOTE
}

picorv32_x32_openparf_remote() {
  local remote_root_quoted
  remote_root_quoted="$(shell_quote "$OPENPARF_REMOTE_ROOT")"
  remote_script <<REMOTE
set -eu
remote_dir="\$1"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
openparf_root=$remote_root_quoted
openparf_python=/home/ziyiwang21/anaconda3/envs/deepgate/bin/python
cd "\$remote_dir"

test -x "\$openparf_python"
test -f "\$openparf_root/OpenPARF-install/openparf.py"
test -s build/remote/phase2-large/xcvu3p.arch.json
test -s build/remote/benchmarks/picorv32-x32-l5/phase1/design.emuir.json
rm -rf \
  build/remote/benchmarks/picorv32-x32-l5/phase2-reference \
  build/remote/benchmarks/picorv32-x32-l5/phase2-openparf \
  build/remote/benchmarks/picorv32-x32-l5/openparf.log

PYTHONPATH=src python3 -m emuflow phase2 \
  --ir build/remote/benchmarks/picorv32-x32-l5/phase1/design.emuir.json \
  --arch build/remote/phase2-large/xcvu3p.arch.json \
  --out build/remote/benchmarks/picorv32-x32-l5/phase2-reference

export CUDA_VISIBLE_DEVICES=""
export PYTHONPATH="\$remote_dir/scripts/openparf/shims:\$openparf_root/OpenPARF-install"
"\$openparf_python" "\$openparf_root/OpenPARF-install/openparf.py" \
  --config \
  build/remote/benchmarks/picorv32-x32-l5/phase2-reference/openparf/openparf.json \
  --log build/remote/benchmarks/picorv32-x32-l5/openparf.log

result=build/remote/benchmarks/picorv32-x32-l5/phase2-reference/openparf/results/picorv32_x32_top.pl
test -s "\$result"
PYTHONPATH=src python3 -m emuflow phase2 \
  --ir build/remote/benchmarks/picorv32-x32-l5/phase1/design.emuir.json \
  --arch build/remote/phase2-large/xcvu3p.arch.json \
  --openparf-result "\$result" \
  --out build/remote/benchmarks/picorv32-x32-l5/phase2-openparf

python3 - <<'PY'
import json
from pathlib import Path

root = Path("build/remote/benchmarks/picorv32-x32-l5")
report = json.loads(
    (root / "phase2-openparf/phase2_report.json").read_text(encoding="utf-8")
)
placement = report["placement"]
status = (
    "pass"
    if placement["cells"] >= 100_000 and placement["status"] == "legal"
    else "fail"
)
print(
    "EMUFLOW_PICORV32_X32_OPENPARF "
    f"status={status} cells={placement['cells']} "
    f"sites_used={placement['sites_used']}"
)
if status != "pass":
    raise SystemExit("100k-cell OpenPARF placement gate failed")
PY
du -sh build/remote/benchmarks/picorv32-x32-l5
REMOTE
}

picorv32_x32_vivado_remote() {
  remote_script <<'REMOTE'
set -eu
remote_dir="$1"
vivado_root="$2"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd "$remote_dir"

root=build/remote/benchmarks/picorv32-x32-l5
test -s "$root/synthesis/mapped.v"
result="$root/phase2-reference/openparf/results/picorv32_x32_top.pl"
test -s "$result"
PYTHONPATH=src python3 -m emuflow phase2 \
  --ir "$root/phase1/design.emuir.json" \
  --arch build/remote/phase2-large/xcvu3p.arch.json \
  --openparf-result "$result" \
  --out "$root/phase2-openparf"
test -s "$root/phase2-openparf/placement.vivado.tsv"
expected_cells="$(python3 -c \
  'import json,sys; print(len(json.load(open(sys.argv[1]))["instances"]))' \
  "$root/phase1/design.emuir.json")"
test "$expected_cells" -ge 100000
rm -rf "$root/vivado"
rm -f "$root/vivado-validation.log"

"$vivado_root/bin/vivado" -mode batch -nojournal -nolog \
  -source scripts/vivado/validate_mapped.tcl \
  -tclargs xcvu3p-ffvc1517-2-e \
  "$root/synthesis/mapped.v" \
  picorv32_x32_top \
  "$root/phase2-openparf/placement.vivado.tsv" \
  "$root/vivado" \
  "$expected_cells" clk 10.0 \
  > "$root/vivado-validation.log" 2>&1
grep 'EMUFLOW_MAPPED_VIVADO status=pass' \
  "$root/vivado-validation.log"
test -s "$root/vivado/routed.dcp"
du -sh "$root/vivado"
REMOTE
}

picorv32_x32_phase3_remote() {
  remote_script <<'REMOTE'
set -eu
remote_dir="$1"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd "$remote_dir"

root=build/remote/benchmarks/picorv32-x32-l5
ir="$root/phase1/design.emuir.json"
connected_root=build/remote/benchmarks/picorv32-l2
connected_ir="$connected_root/phase1/design.emuir.json"
platform=platforms/virtual/xcvu3p_2fpga_p2p.json
output="$root/phase3"
repeat="$root/phase3-repeat"
test -s "$ir"
test -s "$connected_ir"

/usr/bin/time -v -o "$root/phase3-time.txt" \
  env PYTHONPATH=src python3 -m emuflow phase3 \
    --ir "$ir" \
    --platform "$platform" \
    --out "$output" \
    --seed 20260727 \
    > "$root/phase3-stdout.json"

PYTHONPATH=src python3 -m emuflow partition validate \
  "$output/assignment.json" \
  --clusters "$output/clusters.json" \
  --ir "$ir" \
  --platform "$platform" \
  > "$root/phase3-independent-check.json"

PYTHONPATH=src python3 -m emuflow phase3 \
  --ir "$ir" \
  --platform "$platform" \
  --out "$repeat" \
  --seed 20260727 \
  > "$root/phase3-repeat-stdout.json"

first_hash="$(sha256sum "$output/assignment.json" | awk '{print $1}')"
repeat_hash="$(sha256sum "$repeat/assignment.json" | awk '{print $1}')"
test "$first_hash" = "$repeat_hash"

/usr/bin/time -v -o "$connected_root/phase3-time.txt" \
  env PYTHONPATH=src python3 -m emuflow phase3 \
    --ir "$connected_ir" \
    --platform "$platform" \
    --out "$connected_root/phase3" \
    --seed 20260727 \
    > "$connected_root/phase3-stdout.json"

PYTHONPATH=src python3 -m emuflow partition validate \
  "$connected_root/phase3/assignment.json" \
  --clusters "$connected_root/phase3/clusters.json" \
  --ir "$connected_ir" \
  --platform "$platform" \
  > "$connected_root/phase3-independent-check.json"

PYTHONPATH=src python3 -m emuflow phase3 \
  --ir "$connected_ir" \
  --platform "$platform" \
  --out "$connected_root/phase3-repeat" \
  --seed 20260727 \
  > "$connected_root/phase3-repeat-stdout.json"

connected_hash="$(
  sha256sum "$connected_root/phase3/assignment.json" | awk '{print $1}'
)"
connected_repeat_hash="$(
  sha256sum "$connected_root/phase3-repeat/assignment.json" | awk '{print $1}'
)"
test "$connected_hash" = "$connected_repeat_hash"

python3 - <<'PY'
import json
from pathlib import Path

root = Path("build/remote/benchmarks/picorv32-x32-l5")
scale_report = json.loads((root / "phase3/phase3_report.json").read_text())
scale = scale_report["validation"]
scale_partitions = scale_report["partitions"]
if scale_report["status"] != "pass":
    raise SystemExit("Phase 3 scale report did not pass")
if scale["instances"] < 100_000:
    raise SystemExit("Phase 3 did not exercise at least 100,000 cells")
if scale["used_fpgas"] != 2:
    raise SystemExit("Phase 3 scale run did not force both virtual FPGAs")
if scale["illegal_cuts"] != 0:
    raise SystemExit("Phase 3 scale run contains a forbidden cut")
if any(
    partition["instance_count"] < 50_000 for partition in scale_partitions
):
    raise SystemExit("Phase 3 scale partition balance gate failed")

connected_root = Path("build/remote/benchmarks/picorv32-l2")
connected_report = json.loads(
    (connected_root / "phase3/phase3_report.json").read_text()
)
connected = connected_report["validation"]
connected_partitions = connected_report["partitions"]
if connected_report["status"] != "pass":
    raise SystemExit("connected Phase 3 report did not pass")
if connected["instances"] < 3_000:
    raise SystemExit("connected Phase 3 design is unexpectedly small")
if connected["used_fpgas"] != 2:
    raise SystemExit("connected Phase 3 run did not force both virtual FPGAs")
if connected["illegal_cuts"] != 0:
    raise SystemExit("connected Phase 3 run contains a forbidden cut")
if connected["cut_nets"] <= 0:
    raise SystemExit("connected Phase 3 run produced no cross-FPGA cut nets")
if any(
    partition["instance_count"] < 100
    for partition in connected_partitions
):
    raise SystemExit("connected Phase 3 non-empty partition gate failed")
print(
    "EMUFLOW_PICORV32_X32_PHASE3 "
    f"status=pass instances={scale['instances']} "
    f"clusters={scale['clusters']} "
    f"used_fpgas={scale['used_fpgas']} "
    f"cut_nets={scale['cut_nets']} "
    f"illegal_cuts={scale['illegal_cuts']} "
    f"partition_cells="
    f"{','.join(str(item['instance_count']) for item in scale_partitions)}"
)
print(
    "EMUFLOW_PICORV32_CONNECTED_PHASE3 "
    f"status=pass instances={connected['instances']} "
    f"clusters={connected['clusters']} "
    f"used_fpgas={connected['used_fpgas']} "
    f"cut_nets={connected['cut_nets']} "
    f"cut_sink_endpoints={connected['cut_sink_endpoints']} "
    f"illegal_cuts={connected['illegal_cuts']} "
    f"partition_cells="
    f"{','.join(str(item['instance_count']) for item in connected_partitions)}"
)
PY

printf 'scale_assignment_sha256=%s\n' "$first_hash"
printf 'connected_assignment_sha256=%s\n' "$connected_hash"
du -sh \
  "$output" \
  "$repeat" \
  "$connected_root/phase3" \
  "$connected_root/phase3-repeat"
REMOTE
}

picorv32_phase4_remote() {
  remote_script <<'REMOTE'
set -eu
remote_dir="$1"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd "$remote_dir"

root=build/remote/benchmarks/picorv32-l2
assignment="$root/phase3/assignment.json"
platform=platforms/virtual/xcvu3p_2fpga_p2p.json
output="$root/phase4"
repeat="$root/phase4-repeat"
test -s "$assignment"

/usr/bin/time -v -o "$root/phase4-time.txt" \
  env PYTHONPATH=src python3 -m emuflow phase4 \
    --assignment "$assignment" \
    --platform "$platform" \
    --out "$output" \
    --frame-slots 32 \
    > "$root/phase4-stdout.json"

PYTHONPATH=src python3 -m emuflow route validate \
  "$output/routes.json" \
  --assignment "$assignment" \
  --platform "$platform" \
  > "$root/phase4-independent-check.json"

PYTHONPATH=src python3 -m emuflow phase4 \
  --assignment "$assignment" \
  --platform "$platform" \
  --out "$repeat" \
  --frame-slots 32 \
  > "$root/phase4-repeat-stdout.json"

first_hash="$(sha256sum "$output/routes.json" | awk '{print $1}')"
repeat_hash="$(sha256sum "$repeat/routes.json" | awk '{print $1}')"
test "$first_hash" = "$repeat_hash"

python3 - <<'PY'
import json
from pathlib import Path

root = Path("build/remote/benchmarks/picorv32-l2")
report = json.loads((root / "phase4/phase4_report.json").read_text())
validation = report["validation"]
if report["status"] != "pass":
    raise SystemExit("Phase 4 report did not pass")
if validation["demands"] != 140:
    raise SystemExit(
        f"expected 140 real cut-net demands, got {validation['demands']}"
    )
if validation["routed_sinks"] != 140:
    raise SystemExit("not every real cut-net sink was routed")
if validation["overloaded_links"] != 0:
    raise SystemExit("Phase 4 contains an overloaded link")
if validation["tree_edges"] != 140:
    raise SystemExit("two-FPGA route should use one tree edge per demand")
if validation["max_link_utilization"] > 1.0:
    raise SystemExit("Phase 4 exceeds modeled frame capacity")
print(
    "EMUFLOW_PICORV32_PHASE4 "
    f"status=pass demands={validation['demands']} "
    f"routed_sinks={validation['routed_sinks']} "
    f"tree_edges={validation['tree_edges']} "
    f"iterations={validation['iterations']} "
    f"max_link_utilization={validation['max_link_utilization']:.6f} "
    f"total_link_bit_hops={validation['total_link_bit_hops']} "
    f"overloaded_links={validation['overloaded_links']}"
)
for link in validation["link_utilization"]:
    print(
        "EMUFLOW_PHASE4_LINK "
        f"key={link['key']} used_bits={link['used_bits']} "
        f"capacity_bits={link['capacity_bits']} "
        f"utilization={link['utilization']:.6f}"
    )
PY

printf 'routes_sha256=%s\n' "$first_hash"
du -sh "$output" "$repeat"
REMOTE
}

picorv32_phase5_remote() {
  remote_script <<'REMOTE'
set -eu
remote_dir="$1"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd "$remote_dir"

root=build/remote/benchmarks/picorv32-l2
routes="$root/phase4/routes.json"
platform=platforms/virtual/xcvu3p_2fpga_p2p.json
output="$root/phase5"
repeat="$root/phase5-repeat"
test -s "$routes"
test -x /usr/bin/iverilog
test -x /usr/bin/vvp

/usr/bin/time -v -o "$root/phase5-time.txt" \
  env PYTHONPATH=src python3 -m emuflow phase5 \
    --routes "$routes" \
    --platform "$platform" \
    --out "$output" \
    --simulation-frames 64 \
    > "$root/phase5-stdout.json"

PYTHONPATH=src python3 -m emuflow schedule validate \
  "$output/schedule.json" \
  --routes "$routes" \
  --platform "$platform" \
  > "$root/phase5-independent-check.json"

PYTHONPATH=src python3 -m emuflow phase5 \
  --routes "$routes" \
  --platform "$platform" \
  --out "$repeat" \
  --simulation-frames 64 \
  > "$root/phase5-repeat-stdout.json"

first_hash="$(sha256sum "$output/schedule.json" | awk '{print $1}')"
repeat_hash="$(sha256sum "$repeat/schedule.json" | awk '{print $1}')"
test "$first_hash" = "$repeat_hash"

/usr/bin/iverilog -g2012 -s transport_schedule_tb \
  -o "$output/transport_schedule_simv" \
  rtl/transport/emuflow_tdm_link.sv \
  "$output/transport_schedule_tb.sv"
/usr/bin/vvp "$output/transport_schedule_simv" \
  > "$output/transport_schedule_sim.log"
grep 'EMUFLOW_TDM_RTL_SIM status=pass' \
  "$output/transport_schedule_sim.log"

/usr/bin/iverilog -g2012 -s emuflow_frame_barrier \
  -o "$output/frame_barrier_compile" \
  rtl/transport/emuflow_frame_barrier.sv

python3 - <<'PY'
import json
from pathlib import Path

root = Path("build/remote/benchmarks/picorv32-l2")
report = json.loads((root / "phase5/phase5_report.json").read_text())
validation = report["validation"]
simulation = report["simulation"]
if report["status"] != "pass":
    raise SystemExit("Phase 5 report did not pass")
if validation["demands"] != 140:
    raise SystemExit("Phase 5 did not schedule all 140 real demands")
if validation["scheduled_bit_hops"] != 140:
    raise SystemExit("Phase 5 did not schedule every routed bit-hop")
if validation["routed_sinks"] != 140:
    raise SystemExit("Phase 5 did not complete every remote sink")
if validation["collisions"] != 0:
    raise SystemExit("Phase 5 contains a lane/slot collision")
if validation["completion_slot"] >= validation["frame_slots"]:
    raise SystemExit("Phase 5 does not complete within the virtual frame")
if simulation["frames"] != 64:
    raise SystemExit("Phase 5 transport simulation frame count mismatch")
if simulation["delivered_sink_values"] != 64 * 140:
    raise SystemExit("Phase 5 transport simulation lost sink values")
print(
    "EMUFLOW_PICORV32_PHASE5 "
    f"status=pass demands={validation['demands']} "
    f"scheduled_bit_hops={validation['scheduled_bit_hops']} "
    f"routed_sinks={validation['routed_sinks']} "
    f"frame_slots={validation['frame_slots']} "
    f"completion_slot={validation['completion_slot']} "
    f"max_domain_utilization="
    f"{validation['max_domain_utilization']:.6f} "
    f"collisions={validation['collisions']} "
    f"simulation_frames={simulation['frames']} "
    f"delivered_sink_values={simulation['delivered_sink_values']} "
    f"trace_sha256={simulation['trace_sha256']}"
)
PY

printf 'schedule_sha256=%s\n' "$first_hash"
du -sh "$output" "$repeat"
REMOTE
}

picorv32_phase6_remote() {
  remote_script <<'REMOTE'
set -eu
remote_dir="$1"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd "$remote_dir"

root=build/remote/benchmarks/picorv32-l2
mapped_json="$root/synthesis/mapped.json"
ir="$root/phase1/design.emuir.json"
assignment="$root/phase3/assignment.json"
routes="$root/phase4/routes.json"
schedule="$root/phase5/schedule.json"
platform=platforms/virtual/xcvu3p_2fpga_p2p.json
output="$root/phase6"
repeat="$root/phase6-repeat"
test -s "$mapped_json"
test -s "$assignment"
test -s "$routes"
test -x /usr/bin/iverilog

# Re-import the existing real Yosys netlist so Phase 6 retains primitive
# constant connections used by the mapped cycle model.
PYTHONPATH=src python3 -m emuflow phase1 \
  --yosys-json "$mapped_json" \
  --top picorv32 \
  --clock clk \
  --platform "$platform" \
  --out "$root/phase1" \
  > "$root/phase1-phase6-refresh.json"

# Rebuild the schedule with explicit route metadata required by the splitter.
PYTHONPATH=src python3 -m emuflow phase5 \
  --routes "$routes" \
  --platform "$platform" \
  --out "$root/phase5" \
  --simulation-frames 64 \
  > "$root/phase5-phase6-refresh.json"

/usr/bin/time -v -o "$root/phase6-time.txt" \
  env PYTHONPATH=src python3 -m emuflow phase6 \
    --ir "$ir" \
    --assignment "$assignment" \
    --schedule "$schedule" \
    --platform "$platform" \
    --out "$output" \
    --equivalence-cycles 64 \
    --equivalence-seed 20260727 \
    > "$root/phase6-stdout.json"

PYTHONPATH=src python3 -m emuflow split validate \
  "$output/manifest.json" \
  --ir "$ir" \
  --assignment "$assignment" \
  --schedule "$schedule" \
  --platform "$platform" \
  > "$root/phase6-independent-check.json"

PYTHONPATH=src python3 -m emuflow phase6 \
  --ir "$ir" \
  --assignment "$assignment" \
  --schedule "$schedule" \
  --platform "$platform" \
  --out "$repeat" \
  --equivalence-cycles 64 \
  --equivalence-seed 20260727 \
  > "$root/phase6-repeat-stdout.json"

for fpga in fpga0 fpga1; do
  /usr/bin/iverilog -g2012 \
    -s "emuflow_transport_${fpga}" \
    -o "$output/$fpga/transport_compile" \
    "$output/$fpga/transport_schedule.sv" \
    > "$output/$fpga/transport_compile.log" 2>&1
  test -x "$output/$fpga/transport_compile"
done

first_hash="$(
  sha256sum \
    "$output/manifest.json" \
    "$output/lane_map.json" \
    "$output/fpga0/netlist.json" \
    "$output/fpga0/transport.json" \
    "$output/fpga1/netlist.json" \
    "$output/fpga1/transport.json" |
  awk '{print $1}' | sha256sum | awk '{print $1}'
)"
repeat_hash="$(
  sha256sum \
    "$repeat/manifest.json" \
    "$repeat/lane_map.json" \
    "$repeat/fpga0/netlist.json" \
    "$repeat/fpga0/transport.json" \
    "$repeat/fpga1/netlist.json" \
    "$repeat/fpga1/transport.json" |
  awk '{print $1}' | sha256sum | awk '{print $1}'
)"
test "$first_hash" = "$repeat_hash"

python3 - <<'PY'
import json
from pathlib import Path

root = Path("build/remote/benchmarks/picorv32-l2")
report = json.loads((root / "phase6/phase6_report.json").read_text())
validation = report["validation"]
equivalence = report["equivalence"]
if report["status"] != "pass":
    raise SystemExit("Phase 6 report did not pass")
if validation["instances"] != 3812:
    raise SystemExit("Phase 6 real design instance count mismatch")
if validation["scheduled_hops"] != 140:
    raise SystemExit("Phase 6 did not cover all scheduled hops")
if validation["transport_endpoints"] != 280:
    raise SystemExit("Phase 6 did not create paired TX/RX endpoints")
if validation["endpoint_agreement_errors"] != 0:
    raise SystemExit("Phase 6 lane endpoint agreement failed")
if equivalence["cycles"] != 64 or equivalence["mismatches"] != 0:
    raise SystemExit("Phase 6 mapped cycle equivalence failed")
if report["board_binding"]["status"] != "virtual":
    raise SystemExit("Phase 6 incorrectly claims package-pin binding")
print(
    "EMUFLOW_PICORV32_PHASE6 "
    f"status=pass instances={validation['instances']} "
    f"net_segments={validation['net_segments']} "
    f"scheduled_hops={validation['scheduled_hops']} "
    f"transport_endpoints={validation['transport_endpoints']} "
    f"lane_map_entries={validation['lane_map_entries']} "
    f"virtual_anchors={validation['virtual_anchors']} "
    f"unbound_package_pins={validation['unbound_package_pins']} "
    f"equivalence_cycles={equivalence['cycles']} "
    f"compared_state_bits={equivalence['compared_state_bits']} "
    f"compared_output_bits={equivalence['compared_output_bits']} "
    f"mismatches={equivalence['mismatches']} "
    f"trace_sha256={equivalence['trace_sha256']}"
)
PY

printf 'phase6_artifact_set_sha256=%s\n' "$first_hash"
du -sh "$output" "$repeat"
REMOTE
}

picorv32_phase7a_remote() {
  local remote_root_quoted
  remote_root_quoted="$(shell_quote "$OPENPARF_REMOTE_ROOT")"
  remote_script <<REMOTE
set -eu
remote_dir="\$1"
yosys_path="\$3"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
openparf_root=$remote_root_quoted
openparf_python=/home/ziyiwang21/anaconda3/envs/deepgate/bin/python
cd "\$remote_dir"

root=build/remote/benchmarks/picorv32-l2
phase6="\$root/phase6"
phase7a="\$root/phase7a"
arch=build/remote/phase2/xcvu3p.arch.json
test -x "\$yosys_path"
test -x "\$openparf_python"
test -f "\$openparf_root/OpenPARF-install/openparf.py"
test -s "\$arch"
test -s "\$phase6/manifest.json"
mkdir -p "\$phase7a"

/usr/bin/time -v -o "\$root/phase7a-time.txt" \
  /bin/bash --noprofile --norc -s <<'INNER'
set -eu
root=build/remote/benchmarks/picorv32-l2
phase6="\$root/phase6"
phase7a="\$root/phase7a"
arch=build/remote/phase2/xcvu3p.arch.json
yosys_path="$YOSYS_PATH"
openparf_root="$OPENPARF_REMOTE_ROOT"
openparf_python=/home/ziyiwang21/anaconda3/envs/deepgate/bin/python

for fpga in fpga0 fpga1; do
  target="\$phase7a/\$fpga"
  mkdir -p "\$target"
  PYTHONPATH=src python3 -m emuflow synth-yosys \
    "\$phase6/\$fpga/transport_schedule.sv" \
    --top "emuflow_transport_\$fpga" \
    --family xcup \
    --policy logic-only \
    --yosys "\$yosys_path" \
    --output "\$target/transport.mapped.json" \
    --verilog-output "\$target/transport.mapped.v" \
    --log "\$target/transport-yosys.log" \
    > "\$target/transport-synthesis-report.json"
  PYTHONPATH=src python3 -m emuflow import-yosys \
    "\$target/transport.mapped.json" \
    --top "emuflow_transport_\$fpga" \
    --clock fabric_clk \
    --output "\$target/transport.emuir.json" \
    > "\$target/transport-import-report.json"
  PYTHONPATH=src python3 -m emuflow lower-placement-ir \
    --netlist "\$phase6/\$fpga/netlist.json" \
    --transport "\$phase6/\$fpga/transport.json" \
    --transport-ir "\$target/transport.emuir.json" \
    --output "\$target/placement.emuir.json" \
    --report "\$target/lowering-report.json" \
    > "\$target/lowering-stdout.json"
  PYTHONPATH=src python3 -m emuflow phase2 \
    --ir "\$target/placement.emuir.json" \
    --arch "\$arch" \
    --out "\$target/placement-reference" \
    > "\$target/placement-reference-report.json"

  export CUDA_VISIBLE_DEVICES=""
  export PYTHONPATH="\$PWD/scripts/openparf/shims:\$openparf_root/OpenPARF-install"
  "\$openparf_python" "\$openparf_root/OpenPARF-install/openparf.py" \
    --config "\$target/placement-reference/openparf/openparf.json" \
    --log "\$target/openparf.log"
  result="\$target/placement-reference/openparf/results/picorv32__\$fpga.pl"
  test -s "\$result"
  PYTHONPATH=src python3 -m emuflow phase2 \
    --ir "\$target/placement.emuir.json" \
    --arch "\$arch" \
    --openparf-result "\$result" \
    --out "\$target/placement-openparf" \
    > "\$target/placement-openparf-report.json"
done
INNER

python3 - <<'PY'
import json
from pathlib import Path

root = Path("build/remote/benchmarks/picorv32-l2/phase7a")
total_original = 0
total_merged = 0
for fpga in ("fpga0", "fpga1"):
    lowering = json.loads((root / fpga / "lowering-report.json").read_text())
    placement = json.loads(
        (root / fpga / "placement-openparf/phase2_report.json").read_text()
    )["placement"]
    netlist = json.loads(
        (
            Path("build/remote/benchmarks/picorv32-l2/phase6")
            / fpga
            / "netlist.json"
        ).read_text()
    )
    if lowering["status"] != "pass":
        raise SystemExit(f"{fpga} lowering failed")
    if placement["status"] != "legal":
        raise SystemExit(f"{fpga} OpenPARF placement is not legal")
    if placement["cells"] != lowering["instances"]:
        raise SystemExit(f"{fpga} placement lost merged instances")
    if lowering["transport_instances"] <= 0:
        raise SystemExit(f"{fpga} contains no synthesized transport cells")
    total_original += len(netlist["instances"])
    total_merged += lowering["instances"]
    print(
        "EMUFLOW_PICORV32_PHASE7A_FPGA "
        f"status=pass fpga={fpga} "
        f"original_cells={len(netlist['instances'])} "
        f"transport_cells={lowering['transport_instances']} "
        f"merged_cells={lowering['instances']} "
        f"sites_used={placement['sites_used']}"
    )
if total_original != 3812:
    raise SystemExit("Phase 7A original partition coverage mismatch")
print(
    "EMUFLOW_PICORV32_PHASE7A "
    f"status=pass original_cells={total_original} "
    f"merged_cells={total_merged} "
    f"transport_overhead_cells={total_merged-total_original}"
)
PY
REMOTE
}

picorv32_phase7b_remote() {
  remote_script <<'REMOTE'
set -eu
remote_dir="$1"
vivado_root="$2"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd "$remote_dir"

root=build/remote/benchmarks/picorv32-l2
phase7a="$root/phase7a"
phase7b="$root/phase7b"
mkdir -p "$phase7b"
for fpga in fpga0 fpga1; do
  source="$phase7a/$fpga"
  target="$phase7b/$fpga"
  test -s "$source/placement.emuir.json"
  test -s "$source/placement-openparf/placement.vivado.tsv"
  mkdir -p "$target"
  PYTHONPATH=src python3 -m emuflow emit-mapped-verilog \
    --ir "$source/placement.emuir.json" \
    --output "$target/mapped.v" \
    --report "$target/emission-report.json" \
    > "$target/emission-stdout.json"
  expected_cells="$(
    python3 -c \
      'import json,sys; print(json.load(open(sys.argv[1]))["instances"])' \
      "$target/emission-report.json"
  )"
  /usr/bin/time -v -o "$target/vivado-time.txt" \
    "$vivado_root/bin/vivado" -mode batch -nojournal -nolog \
      -source scripts/vivado/validate_mapped.tcl \
      -tclargs xcvu3p-ffvc1517-2-e \
      "$target/mapped.v" \
      "picorv32__$fpga" \
      "$source/placement-openparf/placement.vivado.tsv" \
      "$target/vivado" \
      "$expected_cells" clk 10.0 \
      > "$target/vivado-validation.log" 2>&1
  grep 'EMUFLOW_MAPPED_VIVADO status=pass' \
    "$target/vivado-validation.log"
  test -s "$target/vivado/routed.dcp"
done

python3 - <<'PY'
import json
from pathlib import Path

root = Path("build/remote/benchmarks/picorv32-l2/phase7b")
total = 0
for fpga in ("fpga0", "fpga1"):
    report = json.loads((root / fpga / "emission-report.json").read_text())
    if report["status"] != "pass":
        raise SystemExit(f"{fpga} mapped Verilog emission failed")
    if not (root / fpga / "vivado/routed.dcp").is_file():
        raise SystemExit(f"{fpga} routed checkpoint is missing")
    if not (root / fpga / "vivado/route_status.rpt").is_file():
        raise SystemExit(f"{fpga} route status report is missing")
    total += report["instances"]
    print(
        "EMUFLOW_PICORV32_PHASE7B_FPGA "
        f"status=pass fpga={fpga} cells={report['instances']} "
        f"nets={report['nets']} ports={report['ports']}"
    )
if total != 4197:
    raise SystemExit(f"Phase 7B routed {total} cells; expected 4197")
print(f"EMUFLOW_PICORV32_PHASE7B status=pass routed_cells={total}")
PY
REMOTE
}

koios_dla_medium_synth_remote() {
  remote_script <<'REMOTE'
set -eu
remote_dir="$1"
yosys_path="$3"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd "$remote_dir"

source_root=third_party/rtl/koios/vtr_flow/benchmarks/verilog/koios
test -x "$yosys_path"
test -f "$source_root/dla_like.medium.v"

PYTHONPATH=src python3 -m emuflow benchmark \
  benchmarks/runs/koios_dla_medium_l5.json \
  --source-root "$source_root" \
  --out build/remote/benchmarks/koios-dla-medium-l5 \
  --yosys "$yosys_path"

python3 - <<'PY'
import json
from pathlib import Path

path = Path(
    "build/remote/benchmarks/koios-dla-medium-l5/"
    "phase1/design.emuir.json"
)
design = json.loads(path.read_text(encoding="utf-8"))
counts = {}
for instance in design["instances"]:
    cell_type = instance["type"]
    counts[cell_type] = counts.get(cell_type, 0) + 1
total = len(design["instances"])
print(
    "EMUFLOW_KOIOS_SYNTH "
    f"status={'pass' if total >= 100000 else 'fail'} "
    f"cells={total} types={json.dumps(counts, sort_keys=True)}"
)
if total < 100000:
    raise SystemExit(
        f"mapped design has {total} cells; expected at least 100000"
    )
PY
REMOTE
}

koios_dla_small_synth_remote() {
  remote_script <<'REMOTE'
set -eu
remote_dir="$1"
yosys_path="$3"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd "$remote_dir"

source_root=third_party/rtl/koios/vtr_flow/benchmarks/verilog/koios
test -x "$yosys_path"
test -f "$source_root/dla_like.small.v"
rm -rf build/remote/benchmarks/koios-dla-small-l5

PYTHONPATH=src python3 -m emuflow benchmark \
  benchmarks/runs/koios_dla_small_l5.json \
  --source-root "$source_root" \
  --out build/remote/benchmarks/koios-dla-small-l5 \
  --yosys "$yosys_path"

python3 - <<'PY'
import json
from pathlib import Path

root = Path("build/remote/benchmarks/koios-dla-small-l5")
design = json.loads(
    (root / "phase1/design.emuir.json").read_text(encoding="utf-8")
)
counts = {}
for instance in design["instances"]:
    cell_type = instance["type"]
    counts[cell_type] = counts.get(cell_type, 0) + 1
total = len(design["instances"])
print(
    "EMUFLOW_KOIOS_SYNTH "
    f"status={'pass' if total >= 100000 else 'fail'} "
    f"cells={total} types={json.dumps(counts, sort_keys=True)}"
)
if total < 100000:
    raise SystemExit(
        f"mapped design has {total} cells; expected at least 100000"
    )
PY

du -sh \
  build/remote/benchmarks/koios-dla-small-l5 \
  build/remote/benchmarks/koios-dla-small-l5/synthesis/*
REMOTE
}

build_openparf_remote() {
  local remote_root_quoted
  remote_root_quoted="$(shell_quote "$OPENPARF_REMOTE_ROOT")"
  remote_script <<REMOTE
set -eu
remote_dir="\$1"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
openparf_root=$remote_root_quoted
python=/home/ziyiwang21/anaconda3/envs/deepgate/bin/python
test -x "\$python"
test -f "\$openparf_root/OpenPARF-src/CMakeLists.txt"
# macOS archive metadata can appear as AppleDouble files on Linux and may be
# captured by upstream CMake source globs.
find "\$openparf_root/OpenPARF-src" -type f -name '._*' -delete
if grep -q 'AT_PRIVATE_CASE_TYPE(at::' \
  "\$openparf_root/OpenPARF-src/openparf/util/torch.h"; then
  patch --batch --forward -d "\$openparf_root/OpenPARF-src" -p1 \
    < "\$remote_dir/scripts/openparf/patches/torch-public-dispatch.patch"
fi
if grep -q 'at::rfft' \
  "\$openparf_root/OpenPARF-src/openparf/ops/dct/src/dct2_fft2.cpp"; then
  patch --batch --forward -d "\$openparf_root/OpenPARF-src" -p1 \
    < "\$remote_dir/scripts/openparf/patches/torch-fft-api.patch"
fi
if grep -q 'view_as_complex(buf), {M, N}' \
  "\$openparf_root/OpenPARF-src/openparf/ops/dct/src/dct2_fft2.cpp"; then
  patch --batch --forward -d "\$openparf_root/OpenPARF-src" -p1 \
    < "\$remote_dir/scripts/openparf/patches/torch-fft-shape.patch"
fi
mkdir -p "\$openparf_root/OpenPARF-build" "\$openparf_root/OpenPARF-install"
CUDA_VISIBLE_DEVICES="" cmake \
  -S "\$openparf_root/OpenPARF-src" \
  -B "\$openparf_root/OpenPARF-build" \
  -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DPython3_EXECUTABLE="\$python" \
  -DPYTHON_EXECUTABLE="\$python" \
  -DCMAKE_INSTALL_PREFIX="\$openparf_root/OpenPARF-install" \
  -DENABLE_ROUTER=OFF
CUDA_VISIBLE_DEVICES="" cmake --build "\$openparf_root/OpenPARF-build" \
  --target install --parallel 4
REMOTE
}

run_openparf_remote() {
  local remote_root_quoted
  remote_root_quoted="$(shell_quote "$OPENPARF_REMOTE_ROOT")"
  remote_script <<REMOTE
set -eu
remote_dir="\$1"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
openparf_root=$remote_root_quoted
python=/home/ziyiwang21/anaconda3/envs/deepgate/bin/python
cd "\$remote_dir"
test -s build/remote/phase2/run/openparf/openparf.json
export CUDA_VISIBLE_DEVICES=""
export PYTHONPATH="\$remote_dir/scripts/openparf/shims:\$openparf_root/OpenPARF-install"
"\$python" "\$openparf_root/OpenPARF-install/openparf.py" \
  --config build/remote/phase2/run/openparf/openparf.json \
  --log build/remote/phase2/openparf.log
result=build/remote/phase2/run/openparf/results/counter.pl
test -s "\$result"
PYTHONPATH=src python3 -m emuflow phase2 \
  --ir build/remote/phase2/counter.emuir.json \
  --arch build/remote/phase2/xcvu3p.arch.json \
  --openparf-result "\$result" \
  --out build/remote/phase2/run-openparf
REMOTE
}

command="${1:-}"
cd "$REPO_ROOT"
case "$command" in
  probe)
    probe
    ;;
  sync)
    sync_project
    ;;
  bootstrap)
    bootstrap
    ;;
  test)
    test_remote
    ;;
  synth)
    synth_remote
    ;;
  phase1)
    phase1_remote
    ;;
  phase2-arch)
    phase2_arch_remote
    ;;
  phase2-arch-large)
    phase2_arch_large_remote
    ;;
  phase2)
    phase2_remote
    ;;
  phase2-vivado)
    phase2_vivado_remote
    ;;
  phase2-vivado-openparf)
    phase2_vivado_openparf_remote
    ;;
  serv-sync)
    sync_serv_source
    ;;
  serv-l1)
    serv_l1_remote
    ;;
  serv-l1-all)
    sync_project
    sync_serv_source
    test_remote
    phase2_arch_remote
    serv_l1_remote
    ;;
  picorv32-sync)
    sync_picorv32_source
    ;;
  picorv32-l2)
    picorv32_l2_remote
    ;;
  picorv32-l2-all)
    sync_project
    sync_picorv32_source
    test_remote
    phase2_arch_remote
    picorv32_l2_remote
    ;;
  picorv32-x32-synth)
    picorv32_x32_synth_remote
    ;;
  picorv32-x32-openparf)
    picorv32_x32_openparf_remote
    ;;
  picorv32-x32-vivado)
    picorv32_x32_vivado_remote
    ;;
  picorv32-x32-phase3)
    picorv32_x32_phase3_remote
    ;;
  picorv32-phase4)
    picorv32_phase4_remote
    ;;
  picorv32-phase5)
    picorv32_phase5_remote
    ;;
  picorv32-phase6)
    picorv32_phase6_remote
    ;;
  picorv32-phase7a)
    picorv32_phase7a_remote
    ;;
  picorv32-phase7b)
    picorv32_phase7b_remote
    ;;
  koios-sync)
    sync_koios_source
    ;;
  koios-dla-medium-synth)
    koios_dla_medium_synth_remote
    ;;
  koios-dla-small-synth)
    koios_dla_small_synth_remote
    ;;
  openparf-sync)
    sync_openparf
    ;;
  openparf-build)
    build_openparf_remote
    ;;
  openparf-run)
    run_openparf_remote
    ;;
  phase2-all)
    sync_project
    bootstrap
    test_remote
    synth_remote
    phase1_remote
    phase2_arch_remote
    phase2_remote
    phase2_vivado_remote
    ;;
  all)
    sync_project
    bootstrap
    test_remote
    synth_remote
    phase1_remote
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
