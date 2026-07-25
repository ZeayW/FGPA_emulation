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
  phase2     Export OpenPARF input and create a checked reference placement.
  phase2-vivado
             Apply the checked placement and route it with Vivado.
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
  build/remote/phase2/xcvu3p.sites.tsv 64 \
  > build/remote/phase2/vivado-arch.log 2>&1
PYTHONPATH=src python3 -m emuflow arch import-vivado-tsv \
  build/remote/phase2/xcvu3p.sites.tsv \
  --output build/remote/phase2/xcvu3p.arch.json
REMOTE
}

phase2_remote() {
  remote_script <<'REMOTE'
set -eu
remote_dir="$1"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd "$remote_dir"
test -s build/remote/phase1/design.emuir.json
test -s build/remote/phase2/xcvu3p.arch.json
PYTHONPATH=src python3 -m emuflow phase2 \
  --ir build/remote/phase1/design.emuir.json \
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
  phase2)
    phase2_remote
    ;;
  phase2-vivado)
    phase2_vivado_remote
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
