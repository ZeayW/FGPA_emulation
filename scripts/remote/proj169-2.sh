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
CONTROL_PATH="${EMUFLOW_CONTROL_PATH:-}"

usage() {
  cat <<'EOF'
Usage: scripts/remote/proj169-2.sh COMMAND

Commands:
  probe      Inspect the remote host and available FPGA tools.
  sync       Upload the current committed Git snapshot.
  bootstrap  Create .venv and install a user-space Yosys when needed.
  test       Run the Python unit tests on the remote host.
  synth      Synthesize examples/rtl/counter.v with a real Yosys process.
  phase1     Run Phase 1 from the remotely synthesized Yosys JSON.
  all        Run sync, bootstrap, test, synth, and phase1.

Environment overrides:
  EMUFLOW_SSH_ALIAS
  EMUFLOW_INNER_HOST
  EMUFLOW_INNER_PORT
  EMUFLOW_REMOTE_DIR
  EMUFLOW_REMOTE_KNOWN_HOSTS
  EMUFLOW_VIVADO_ROOT
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
    "$command"
}

remote_script() {
  local remote_dir_quoted
  local vivado_root_quoted
  local command
  remote_dir_quoted="$(shell_quote "$REMOTE_DIR")"
  vivado_root_quoted="$(shell_quote "$VIVADO_ROOT")"
  command="$(inner_ssh_command \
    "/bin/bash --noprofile --norc -s -- $remote_dir_quoted $vivado_root_quoted")"
  gateway_ssh "$command"
}

probe() {
  remote_script <<'REMOTE'
set -eu
remote_dir="$1"
vivado_root="$2"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

printf 'host=%s\n' "$(hostname)"
printf 'user=%s\n' "$(id -un)"
printf 'remote_dir=%s\n' "$remote_dir"
for tool in python3 git yosys openparf openparf.py cmake ninja; do
  if command -v "$tool" >/dev/null 2>&1; then
    printf '%s=%s\n' "$tool" "$(command -v "$tool")"
  else
    printf '%s=MISSING\n' "$tool"
  fi
done
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
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd "$remote_dir"

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
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd "$remote_dir"
mkdir -p build/remote
PYTHONPATH=src python3 -m emuflow synth-yosys \
  examples/rtl/counter.v \
  --top counter \
  --family xcup \
  --yosys .venv/bin/yowasp-yosys \
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
