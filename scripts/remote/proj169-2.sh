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
OPENROAD_ROOT="${EMUFLOW_OPENROAD_ROOT:-/home/ziyiwang21/work/tools/openroad-2.0-17598-ga008522d8}"
OPENROAD_PATH="${EMUFLOW_OPENROAD:-$OPENROAD_ROOT/bin/openroad}"
REPART_ROOT="${EMUFLOW_REPART_ROOT:-/home/ziyiwang21/work/tools/repart-211a9d8}"
REPART_PATH="${EMUFLOW_REPART:-$REPART_ROOT/bin/repart}"

usage() {
  cat <<'EOF'
Usage: scripts/remote/proj169-2.sh COMMAND

Commands:
  probe      Inspect the remote host and available FPGA tools.
  sync       Upload the current committed Git snapshot.
  bootstrap  Select server Yosys or install a user-space fallback.
  tritonpart-bootstrap
             Install the pinned OpenROAD/TritonPart binary in user space.
  repart-bootstrap
             Build the pinned, replication-switchable RePart in user space.
  repart-phase3-smoke
             Run RePart twice on real synthesized counter RTL and compare.
  repart-phase3-picorv32
             Validate RePart on connected PicoRV32 and the 121k-cell x32 RTL.
  repart-phase3-nvdla
             Validate RePart twice on the 731,313-cell connected NVDLA design.
  repart-nvdla-downstream
             Run frozen Phases 4-6 and initial Phase 7C on RePart NVDLA.
  repart-nvdla-phase7a
             Lower and place all four RePart NVDLA partitions with OpenPARF.
  repart-nvdla-phase7b
             Emit and route all four RePart NVDLA partitions with Vivado.
  repart-nvdla-phase7c-finalize
             Validate the four routed DCPs and finalize physical/runtime QoR.
  test       Run the Python unit tests on the remote host.
  synth      Synthesize examples/rtl/counter.v with a real Yosys process.
  phase1     Run Phase 1 from the remotely synthesized Yosys JSON.
  phase2-arch
             Export a real xcvu3p Site/BEL inventory with Vivado.
  phase2-arch-large
             Export up to 32,768 xcvu3p SLICE sites for 100k-cell runs.
  nvdla-arch-full
             Export the complete xcvu3p SLICE inventory for NVDLA placement.
  nvdla-arch-vu9p-full
             Export the complete xcvu9p SLICE inventory for the CACC flow.
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
  picorv32-phase7c
             Build/verify the virtual runtime contract and reroute with both clocks.
  picorv32-phase7c-finalize
             Recheck existing runtime-constrained DCPs and aggregate QoR.
  picorv32-phase7c-all
             Rebuild Phase 6/7A, then run the complete Phase 7C validation.
  picorv32-phase7d
             Audit, hash, and seal the complete board-independent G0-G9 run.
  koios-sync Upload the pinned Koios DLA small/medium sources.
  koios-dla-small-synth
             Synthesize DLA-small and require at least 100,000 mapped cells.
  koios-dla-medium-synth
             Synthesize DLA-medium and require at least 100,000 mapped cells.
  veer-eh1-sync
             Upload the pinned upstream VeeR EH1 source checkout.
  veer-eh1-screen
             Generate the upstream FPGA config and measure a real Vivado synthesis.
  nvdla-sync Upload the pinned NVDLA nvdlav1 source archive.
  nvdla-screen
             Synthesize the connected NV_nvdla top and enforce the scale gate.
  nvdla-partition-a-synth
             Map the connected CACC partition and its SRAMs to FPGA soft logic.
  openparf-sync
             Upload an existing local OpenPARF source checkout.
  openparf-build
             Build a CPU OpenPARF in the server deepgate environment.
  openparf-build-sparse
             Build an isolated greedy-first legalizer for sparse large devices.
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
  EMUFLOW_OPENROAD_ROOT
  EMUFLOW_OPENROAD
  EMUFLOW_REPART_ROOT
  EMUFLOW_REPART
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
  local openroad_path_quoted
  local repart_root_quoted
  local repart_path_quoted
  local command
  remote_dir_quoted="$(shell_quote "$REMOTE_DIR")"
  vivado_root_quoted="$(shell_quote "$VIVADO_ROOT")"
  yosys_path_quoted="$(shell_quote "$YOSYS_PATH")"
  openroad_path_quoted="$(shell_quote "$OPENROAD_PATH")"
  repart_root_quoted="$(shell_quote "$REPART_ROOT")"
  repart_path_quoted="$(shell_quote "$REPART_PATH")"
  command="$(inner_ssh_command \
    "/bin/bash --noprofile --norc -s -- $remote_dir_quoted $vivado_root_quoted $yosys_path_quoted $openroad_path_quoted $repart_root_quoted $repart_path_quoted")"
  gateway_ssh "$command"
}

probe() {
  remote_script <<'REMOTE'
set -eu
remote_dir="$1"
vivado_root="$2"
yosys_path="$3"
openroad_path="$4"
repart_path="$6"
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
if [ -x "$openroad_path" ]; then
  printf 'openroad=%s\n' "$openroad_path"
  "$openroad_path" -version
else
  printf 'openroad=MISSING\n'
fi
if [ -x "$repart_path" ]; then
  printf 'repart=%s\n' "$repart_path"
else
  printf 'repart=MISSING\n'
fi
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

tritonpart_bootstrap() {
  local package_name
  local package_url
  local package_sha256
  local cache_dir
  local cache_package
  local cache_temporary
  local remote_root
  local remote_package
  local upload_command

  package_name="openroad_2.0-17598-ga008522d8_amd64-ubuntu-22.04.deb"
  package_url="https://github.com/Precision-Innovations/OpenROAD/releases/download/2024-12-14/$package_name"
  package_sha256="40ed178396b0276a5d5dfbbe695c9de9aac9088157a6655be02b39a0cef07207"
  cache_dir="${XDG_CACHE_HOME:-$HOME/.cache}/emuflow"
  cache_package="$cache_dir/$package_name"
  cache_temporary="$cache_package.download.$$"
  remote_root="$(dirname "$(dirname "$OPENROAD_PATH")")"
  remote_package="$remote_root/$package_name"

  if ! remote_script <<REMOTE
set -eu
package=$(shell_quote "$remote_package")
sha256=$(shell_quote "$package_sha256")
test -s "\$package"
test "\$(sha256sum "\$package" | awk '{print \$1}')" = "\$sha256"
REMOTE
  then
    mkdir -p "$cache_dir"
    local_hash=""
    if [ -s "$cache_package" ]; then
      if command -v sha256sum >/dev/null 2>&1; then
        local_hash="$(sha256sum "$cache_package" | awk '{print $1}')"
      else
        local_hash="$(shasum -a 256 "$cache_package" | awk '{print $1}')"
      fi
    fi
    if [ "$local_hash" != "$package_sha256" ]; then
      curl -L --fail --retry 3 -o "$cache_temporary" "$package_url"
      if command -v sha256sum >/dev/null 2>&1; then
        local_hash="$(sha256sum "$cache_temporary" | awk '{print $1}')"
      else
        local_hash="$(shasum -a 256 "$cache_temporary" | awk '{print $1}')"
      fi
      test "$local_hash" = "$package_sha256"
      mv "$cache_temporary" "$cache_package"
    fi
    upload_command="$(inner_ssh_command \
      "mkdir -p $(shell_quote "$remote_root") && \
       cat > $(shell_quote "$remote_package.upload") && \
       test \\\$(sha256sum $(shell_quote "$remote_package.upload") | awk '{print \\\$1}') = $(shell_quote "$package_sha256") && \
       mv $(shell_quote "$remote_package.upload") $(shell_quote "$remote_package")")"
    gateway_ssh "$upload_command" < "$cache_package"
  fi

  remote_script <<'REMOTE'
set -eu
remote_dir="$1"
openroad_path="$4"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

root="$(dirname "$(dirname "$openroad_path")")"
package="$root/openroad_2.0-17598-ga008522d8_amd64-ubuntu-22.04.deb"
extract="$root/extract"
sha256="40ed178396b0276a5d5dfbbe695c9de9aac9088157a6655be02b39a0cef07207"

mkdir -p "$root" "$extract" "$(dirname "$openroad_path")"
printf '%s  %s\n' "$sha256" "$package" | sha256sum -c -
command -v dpkg-deb >/dev/null
dpkg-deb -x "$package" "$extract"
candidate="$(
  find "$extract" -type f -path '*/bin/openroad' -perm -u+x | head -n 1
)"
test -n "$candidate"
cat > "$openroad_path" <<EOF
#!/usr/bin/env bash
export LD_LIBRARY_PATH="$extract/opt/or-tools/lib:$extract/usr/lib:$extract/usr/lib/x86_64-linux-gnu:\${LD_LIBRARY_PATH:-}"
exec "$candidate" "\$@"
EOF
chmod +x "$openroad_path"
"$openroad_path" -version

probe_tcl="$root/probe_tritonpart.tcl"
cat > "$probe_tcl" <<'EOF'
help triton_part_hypergraph
exit
EOF
"$openroad_path" -exit "$probe_tcl" > "$root/probe_tritonpart.log"
grep -q triton_part_hypergraph "$root/probe_tritonpart.log"
printf 'tritonpart=%s\n' "$openroad_path"
REMOTE
}

repart_bootstrap() {
  local upstream
  local commit
  local cache_dir
  local cache_source
  local cache_package
  local cache_temporary
  local package_sha256
  local remote_package
  local upload_command

  upstream="https://github.com/Welement-zyf/RePart.git"
  commit="211a9d8fd526576387cad7ac6dd3531354aeb31c"
  cache_dir="${XDG_CACHE_HOME:-$HOME/.cache}/emuflow"
  cache_source="$cache_dir/repart-$commit"
  cache_package="$cache_dir/repart-$commit.tar.gz"
  cache_temporary="$cache_package.build.$$"
  remote_package="$REPART_ROOT/repart-$commit.tar.gz"

  mkdir -p "$cache_dir"
  if [ ! -s "$cache_package" ]; then
    if [ ! -d "$cache_source/.git" ]; then
      git clone "$upstream" "$cache_source"
    fi
    test "$(git -C "$cache_source" rev-parse HEAD)" = "$commit"
    git -C "$cache_source" archive \
      --format=tar.gz \
      --prefix=upstream/ \
      -o "$cache_temporary" \
      "$commit"
    mv "$cache_temporary" "$cache_package"
  fi
  if command -v sha256sum >/dev/null 2>&1; then
    package_sha256="$(sha256sum "$cache_package" | awk '{print $1}')"
  else
    package_sha256="$(shasum -a 256 "$cache_package" | awk '{print $1}')"
  fi

  if ! remote_script <<REMOTE
set -eu
package=$(shell_quote "$remote_package")
sha256=$(shell_quote "$package_sha256")
test -s "\$package"
test "\$(sha256sum "\$package" | awk '{print \$1}')" = "\$sha256"
REMOTE
  then
    upload_command="$(inner_ssh_command \
      "mkdir -p $(shell_quote "$REPART_ROOT") && \
       cat > $(shell_quote "$remote_package.upload") && \
       mv $(shell_quote "$remote_package.upload") $(shell_quote "$remote_package")")"
    gateway_ssh "$upload_command" < "$cache_package"
  fi

  remote_script <<REMOTE
set -eu
remote_dir="\$1"
repart_root="\$5"
repart_path="\$6"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

commit=$(shell_quote "$commit")
package=$(shell_quote "$remote_package")
package_sha256=$(shell_quote "$package_sha256")
source="\$repart_root/upstream"
patch="\$remote_dir/third_party/patches/repart-phase3a-disable-replication.patch"

command -v g++ >/dev/null
command -v patch >/dev/null
test -f "\$patch"
printf '%s  %s\n' "\$package_sha256" "\$package" | sha256sum -c -
mkdir -p "\$repart_root" "\$(dirname "\$repart_path")"

if [ -d "\$source" ] &&
   { [ ! -f "\$source/.emuflow-upstream-commit" ] ||
     [ "\$(cat "\$source/.emuflow-upstream-commit")" != "\$commit" ]; }; then
  stale="\$source.stale.\$(date +%Y%m%d%H%M%S)"
  mv "\$source" "\$stale"
fi
if [ ! -d "\$source" ]; then
  tar -xzf "\$package" -C "\$repart_root"
  printf '%s\n' "\$commit" > "\$source/.emuflow-upstream-commit"
fi
if patch --dry-run --reverse --silent -d "\$source" -p1 < "\$patch"; then
  :
else
  patch --dry-run --silent -d "\$source" -p1 < "\$patch"
  patch --batch -d "\$source" -p1 < "\$patch"
fi

g++ -Ofast -DNDEBUG \
  -o "\$repart_path.build" "\$source/RePart/partitioner.cpp" \
  -I"\$source/boost_1_86_0/include" \
  -L"\$source/boost_1_86_0/lib" \
  -static -lboost_thread -lboost_system -pthread
mv "\$repart_path.build" "\$repart_path"
chmod +x "\$repart_path"
printf '%s\n' "\$commit" > "\$repart_root/upstream.commit"

set +e
usage="\$("\$repart_path" 2>&1)"
status=\$?
set -e
test "\$status" -ne 0
printf '%s\n' "\$usage" | grep -q -- '\[-r 0|1\]'
printf 'repart=%s\n' "\$repart_path"
printf 'upstream_commit=%s\n' "\$(cat "\$repart_root/upstream.commit")"
REMOTE
}

repart_phase3_smoke_remote() {
  remote_script <<'REMOTE'
set -eu
remote_dir="$1"
repart_path="$6"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd "$remote_dir"

test -x "$repart_path"
test -s build/remote/phase1/design.emuir.json
for run in run1 run2; do
  PYTHONPATH=src python3 -m emuflow phase3 \
    --ir build/remote/phase1/design.emuir.json \
    --platform platforms/virtual/xcvu3p_2fpga_p2p.json \
    --out "build/remote/repart-phase3-smoke/$run" \
    --provider repart \
    --repart "$repart_path" \
    --min-used-fpgas 2 \
    --balance-tolerance 0.5
  PYTHONPATH=src python3 -m emuflow partition validate \
    "build/remote/repart-phase3-smoke/$run/assignment.json" \
    --clusters "build/remote/repart-phase3-smoke/$run/clusters.json" \
    --ir build/remote/phase1/design.emuir.json \
    --platform platforms/virtual/xcvu3p_2fpga_p2p.json
done
cmp \
  build/remote/repart-phase3-smoke/run1/assignment.json \
  build/remote/repart-phase3-smoke/run2/assignment.json
sha256sum \
  build/remote/repart-phase3-smoke/run1/assignment.json \
  build/remote/repart-phase3-smoke/run2/assignment.json
REMOTE
}

repart_phase3_picorv32_remote() {
  remote_script <<'REMOTE'
set -eu
remote_dir="$1"
repart_path="$6"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd "$remote_dir"

platform=platforms/virtual/xcvu3p_2fpga_p2p.json
connected_root=build/remote/benchmarks/picorv32-l2
connected_ir="$connected_root/phase1/design.emuir.json"
scale_root=build/remote/benchmarks/picorv32-x32-l5
scale_ir="$scale_root/phase1/design.emuir.json"
test -x "$repart_path"
test -s "$connected_ir"
test -s "$scale_ir"

for design in connected scale; do
  if [ "$design" = connected ]; then
    root="$connected_root"
    ir="$connected_ir"
  else
    root="$scale_root"
    ir="$scale_ir"
  fi
  for run in run1 run2; do
    output="$root/phase3-repart-$run"
    /usr/bin/time -v -o "$root/phase3-repart-$run-time.txt" \
      env PYTHONPATH=src python3 -m emuflow phase3 \
        --ir "$ir" \
        --platform "$platform" \
        --out "$output" \
        --provider repart \
        --repart "$repart_path" \
        --min-used-fpgas 2 \
        > "$root/phase3-repart-$run-stdout.json"
    PYTHONPATH=src python3 -m emuflow partition validate \
      "$output/assignment.json" \
      --clusters "$output/clusters.json" \
      --ir "$ir" \
      --platform "$platform" \
      > "$root/phase3-repart-$run-independent-check.json"
  done
  cmp \
    "$root/phase3-repart-run1/assignment.json" \
    "$root/phase3-repart-run2/assignment.json"
done

python3 - <<'PY'
import json
from pathlib import Path

designs = {
    "connected": Path("build/remote/benchmarks/picorv32-l2"),
    "scale": Path("build/remote/benchmarks/picorv32-x32-l5"),
}
for name, root in designs.items():
    report = json.loads(
        (root / "phase3-repart-run1/phase3_report.json").read_text()
    )
    validation = report["validation"]
    if report["status"] != "pass":
        raise SystemExit(f"{name} RePart report did not pass")
    if validation["used_fpgas"] != 2:
        raise SystemExit(f"{name} RePart did not use both FPGAs")
    if validation["illegal_cuts"] != 0:
        raise SystemExit(f"{name} RePart produced illegal cuts")
    if name == "connected" and validation["cut_nets"] <= 0:
        raise SystemExit("connected RePart produced no cross-FPGA cuts")
    if name == "scale" and validation["instances"] < 100_000:
        raise SystemExit("scale RePart did not cover 100k cells")
    print(
        "EMUFLOW_REPART_PICORV32 "
        f"design={name} status=pass "
        f"instances={validation['instances']} "
        f"clusters={validation['clusters']} "
        f"cut_nets={validation['cut_nets']} "
        f"partition_cells="
        f"{','.join(str(item['instance_count']) for item in report['partitions'])}"
    )
PY

sha256sum \
  "$connected_root/phase3-repart-run1/assignment.json" \
  "$connected_root/phase3-repart-run2/assignment.json" \
  "$scale_root/phase3-repart-run1/assignment.json" \
  "$scale_root/phase3-repart-run2/assignment.json"
REMOTE
}

repart_phase3_nvdla_remote() {
  remote_script <<'REMOTE'
set -eu
remote_dir="$1"
repart_path="$6"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd "$remote_dir"

baseline_root=/data/zywang/emuflow/nvdla-balanced-phase3b/balanced-flow
ir="$baseline_root/phase1/design.emuir.json"
platform=platforms/virtual/xcvu9p_4fpga_mesh.json
root=/data/zywang/emuflow/nvdla-repart-phase3a
test -x "$repart_path"
test -s "$ir"
test -s "$baseline_root/phase3/assignment.json"
mkdir -p "$root"

for run in run1 run2; do
  output="$root/$run"
  /usr/bin/time -v -o "$root/$run-time.txt" \
    env PYTHONPATH=src python3 -m emuflow phase3 \
      --ir "$ir" \
      --platform "$platform" \
      --out "$output" \
      --provider repart \
      --repart "$repart_path" \
      --repart-timeout-seconds 7200 \
      --min-used-fpgas 4 \
      --balance-tolerance 0.10 \
      > "$root/$run-stdout.json"
  PYTHONPATH=src python3 -m emuflow partition validate \
    "$output/assignment.json" \
    --clusters "$output/clusters.json" \
    --ir "$ir" \
    --platform "$platform" \
    > "$root/$run-independent-check.json"
done

cmp "$root/run1/assignment.json" "$root/run2/assignment.json"

python3 - <<'PY'
import json
from pathlib import Path

root = Path("/data/zywang/emuflow/nvdla-repart-phase3a")
baseline_root = Path(
    "/data/zywang/emuflow/nvdla-balanced-phase3b/balanced-flow"
)
report = json.loads((root / "run1/phase3_report.json").read_text())
baseline = json.loads(
    (baseline_root / "phase3/phase3_report.json").read_text()
)
validation = report["validation"]
if report["status"] != "pass":
    raise SystemExit("NVDLA RePart report did not pass")
if validation["instances"] != 731_313:
    raise SystemExit("NVDLA RePart instance coverage mismatch")
if validation["clusters"] != 399_211:
    raise SystemExit("NVDLA RePart cluster coverage mismatch")
if validation["used_fpgas"] != 4:
    raise SystemExit("NVDLA RePart did not use all four FPGAs")
if validation["illegal_cuts"] != 0:
    raise SystemExit("NVDLA RePart produced illegal cuts")
summary = {
    "schema": "emuflow.repart-phase3-comparison/v1",
    "design": "NV_NVDLA_partition_a",
    "repart": {
        "provider": report["provider"],
        "validation": validation,
        "partition_cells": [
            item["instance_count"] for item in report["partitions"]
        ],
    },
    "tritonpart_baseline": {
        "provider": baseline["provider"],
        "validation": baseline["validation"],
        "partition_cells": [
            item["instance_count"] for item in baseline["partitions"]
        ],
    },
}
(root / "comparison.json").write_text(
    json.dumps(summary, indent=2, sort_keys=True) + "\n"
)
print(
    "EMUFLOW_REPART_NVDLA status=pass "
    f"instances={validation['instances']} "
    f"clusters={validation['clusters']} "
    f"cut_nets={validation['cut_nets']} "
    f"cut_sink_endpoints={validation['cut_sink_endpoints']} "
    f"effective_balance_percent="
    f"{validation['effective_balance_percent']} "
    f"partition_cells="
    f"{','.join(str(item['instance_count']) for item in report['partitions'])}"
)
print(
    "EMUFLOW_REPART_NVDLA_BASELINE "
    f"tritonpart_cut_nets={baseline['validation']['cut_nets']} "
    f"repart_cut_nets={validation['cut_nets']}"
)
PY

sha256sum "$root/run1/assignment.json" "$root/run2/assignment.json"
for run in run1 run2; do
  grep -E 'Elapsed \\(wall clock\\)|Maximum resident set size' \
    "$root/$run-time.txt"
done
du -sh "$root/run1" "$root/run2"
REMOTE
}

repart_nvdla_downstream_remote() {
  remote_script <<'REMOTE'
set -eu
remote_dir="$1"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd "$remote_dir"

root=/data/zywang/emuflow/nvdla-repart-phase3a
flow="$root/flow"
phase3="$root/run1"
baseline=/data/zywang/emuflow/nvdla-balanced-phase3b/balanced-flow
ir="$baseline/phase1/design.emuir.json"
platform=platforms/virtual/xcvu9p_4fpga_mesh.json
test -s "$ir"
test -s "$phase3/assignment.json"
mkdir -p "$flow"

/usr/bin/time -v -o "$flow/phase4-time.txt" \
  env PYTHONPATH=src python3 -m emuflow phase4 \
    --assignment "$phase3/assignment.json" \
    --platform "$platform" \
    --frame-slots 4096 \
    --out "$flow/phase4" \
    > "$flow/phase4-stdout.json"
PYTHONPATH=src python3 -m emuflow route validate \
  "$flow/phase4/routes.json" \
  --assignment "$phase3/assignment.json" \
  --platform "$platform" \
  > "$flow/phase4-independent-check.json"

/usr/bin/time -v -o "$flow/phase5-time.txt" \
  env PYTHONPATH=src python3 -m emuflow phase5 \
    --routes "$flow/phase4/routes.json" \
    --platform "$platform" \
    --simulation-frames 16 \
    --out "$flow/phase5" \
    > "$flow/phase5-stdout.json"

/usr/bin/time -v -o "$flow/phase6-time.txt" \
  env PYTHONPATH=src python3 -m emuflow phase6 \
    --ir "$ir" \
    --assignment "$phase3/assignment.json" \
    --schedule "$flow/phase5/schedule.json" \
    --platform "$platform" \
    --equivalence-cycles 2 \
    --equivalence-seed 20260727 \
    --out "$flow/phase6" \
    > "$flow/phase6-stdout.json"
PYTHONPATH=src python3 -m emuflow split validate \
  "$flow/phase6/manifest.json" \
  --ir "$ir" \
  --assignment "$phase3/assignment.json" \
  --schedule "$flow/phase5/schedule.json" \
  --platform "$platform" \
  > "$flow/phase6-independent-check.json"

PYTHONPATH=src python3 -m emuflow phase7c \
  --schedule "$flow/phase5/schedule.json" \
  --platform "$platform" \
  --phase3-report "$phase3/phase3_report.json" \
  --phase4-report "$flow/phase4/phase4_report.json" \
  --phase5-report "$flow/phase5/phase5_report.json" \
  --phase6-report "$flow/phase6/phase6_report.json" \
  --simulation-frames 64 \
  --out "$flow/phase7c" \
  > "$flow/phase7c-initial-stdout.json"

python3 - <<'PY'
import json
from pathlib import Path

root = Path("/data/zywang/emuflow/nvdla-repart-phase3a")
flow = root / "flow"
phase3 = json.loads((root / "run1/phase3_report.json").read_text())
phase4 = json.loads((flow / "phase4/phase4_report.json").read_text())
phase5 = json.loads((flow / "phase5/phase5_report.json").read_text())
phase6 = json.loads((flow / "phase6/phase6_report.json").read_text())
for name, report in (
    ("phase3", phase3),
    ("phase4", phase4),
    ("phase5", phase5),
    ("phase6", phase6),
):
    if report["status"] != "pass":
        raise SystemExit(f"{name} report did not pass")
if phase4["validation"]["demands"] != phase3["validation"]["cut_nets"]:
    raise SystemExit("Phase 3/4 demand count mismatch")
if phase5["validation"]["demands"] != phase4["validation"]["demands"]:
    raise SystemExit("Phase 4/5 demand count mismatch")
summary = {
    "schema": "emuflow.repart-nvdla-downstream/v1",
    "phase3": phase3["validation"],
    "phase4": phase4["validation"],
    "phase5": phase5["validation"],
    "phase6": phase6["validation"],
}
(flow / "downstream-summary.json").write_text(
    json.dumps(summary, indent=2, sort_keys=True) + "\n"
)
print(
    "EMUFLOW_REPART_NVDLA_DOWNSTREAM status=pass "
    f"demands={phase4['validation']['demands']} "
    f"routed_sinks={phase4['validation']['routed_sinks']} "
    f"bit_hops={phase4['validation']['total_link_bit_hops']} "
    f"max_link_utilization="
    f"{phase4['validation']['max_link_utilization']} "
    f"scheduled_hops={phase5['validation']['scheduled_bit_hops']} "
    f"completion_slot={phase5['validation']['completion_slot']} "
    f"collisions={phase5['validation']['collisions']}"
)
PY

for phase in phase4 phase5 phase6; do
  printf '%s\n' "$phase"
  grep -E 'Elapsed \\(wall clock\\)|Maximum resident set size' \
    "$flow/$phase-time.txt"
done
du -sh "$flow/phase4" "$flow/phase5" "$flow/phase6"
REMOTE
}

repart_nvdla_physical_remote() {
  local phase="$1"
  remote_script <<REMOTE
set -eu
remote_dir="\$1"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

source_root=/data/zywang/emuflow/nvdla-repart-phase3a
root="\$source_root/full-flow"
baseline=/data/zywang/emuflow/nvdla-balanced-phase3b/balanced-flow
mkdir -p "\$root"
ln -sfn "\$baseline/phase1" "\$root/phase1"
ln -sfn "\$source_root/run1" "\$root/phase3"
for stage in phase4 phase5 phase6 phase7c; do
  ln -sfn "\$source_root/flow/\$stage" "\$root/\$stage"
done

test -s "\$root/phase1/design.emuir.json"
test -s "\$root/phase3/assignment.json"
test -s "\$root/phase6/manifest.json"
cd "\$remote_dir"
/usr/bin/time -v -o "\$root/${phase}-total-time.txt" \
  env EMUFLOW_REPO="\$remote_dir" \
    scripts/remote/nvdla_partition_a_balanced.sh ${phase} "\$root"
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

nvdla_arch_full_remote() {
  remote_script <<'REMOTE'
set -eu
remote_dir="$1"
vivado_root="$2"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd "$remote_dir"
mkdir -p build/remote/nvdla-arch-full
"$vivado_root/bin/vivado" -mode batch -nojournal -nolog \
  -source scripts/vivado/export_architecture.tcl \
  -tclargs xcvu3p-ffvc1517-2-e \
  build/remote/nvdla-arch-full/xcvu3p.sites.tsv \
  > build/remote/nvdla-arch-full/vivado-arch.log 2>&1
PYTHONPATH=src python3 -m emuflow arch import-vivado-tsv \
  build/remote/nvdla-arch-full/xcvu3p.sites.tsv \
  --output build/remote/nvdla-arch-full/xcvu3p.arch.json
grep 'EMUFLOW_ARCH_EXPORT' \
  build/remote/nvdla-arch-full/vivado-arch.log
du -sh build/remote/nvdla-arch-full
REMOTE
}

nvdla_arch_vu9p_full_remote() {
  remote_script <<'REMOTE'
set -eu
remote_dir="$1"
vivado_root="$2"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd "$remote_dir"
vivado="$vivado_root/bin/vivado"
output=build/remote/nvdla-arch-vu9p-full
test -x "$vivado"
test -f scripts/vivado/export_architecture.tcl
rm -rf "$output"
mkdir -p "$output"
/usr/bin/time -v -o "$output/vivado-time.txt" \
  "$vivado" -mode batch -nojournal -nolog \
    -source scripts/vivado/export_architecture.tcl \
    -tclargs xcvu9p-flga2104-2L-e \
      "$output/xcvu9p.sites.tsv" \
    > "$output/vivado-arch.log" 2>&1
/usr/bin/time -v -o "$output/import-time.txt" \
  env PYTHONPATH=src python3 -m emuflow arch import-vivado-tsv \
    "$output/xcvu9p.sites.tsv" \
    --output "$output/xcvu9p.arch.json" \
    > "$output/import.log" 2>&1
test -s "$output/xcvu9p.arch.json"
grep 'EMUFLOW_ARCH_EXPORT' "$output/vivado-arch.log"
du -sh "$output"
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

sync_veer_eh1_source() {
  local source="$REPO_ROOT/third_party/rtl/veer_eh1"
  local destination_quoted
  local unpack_command

  env \
    -u ALL_PROXY -u HTTPS_PROXY -u HTTP_PROXY \
    -u all_proxy -u https_proxy -u http_proxy \
    python3 "$REPO_ROOT/scripts/benchmarks/fetch.py" fetch veer_eh1
  if [ ! -f "$source/veer.core" ] ||
    [ ! -f "$source/design/veer_wrapper.sv" ] ||
    [ ! -x "$source/configs/veer.config" ]; then
    echo "error: VeeR EH1 source is incomplete at $source" >&2
    return 1
  fi
  destination_quoted="$(shell_quote "$REMOTE_DIR/third_party/rtl/veer_eh1")"
  unpack_command="$(inner_ssh_command \
    "mkdir -p $destination_quoted && tar -xf - -C $destination_quoted")"
  COPYFILE_DISABLE=1 tar -cf - --exclude=.git -C "$source" . |
    gateway_ssh "$unpack_command"
}

sync_nvdla_source() {
  local source="$REPO_ROOT/third_party/rtl/nvdla"
  local destination_quoted
  local unpack_command

  env \
    -u ALL_PROXY -u HTTPS_PROXY -u HTTP_PROXY \
    -u all_proxy -u https_proxy -u http_proxy \
    python3 "$REPO_ROOT/scripts/benchmarks/fetch.py" fetch nvdla
  if [ ! -f "$source/.emuflow-source.json" ] ||
    [ ! -f "$source/vmod/nvdla/top/NV_nvdla.v" ] ||
    [ ! -d "$source/vmod/rams/synth" ]; then
    echo "error: NVDLA source is incomplete at $source" >&2
    return 1
  fi
  destination_quoted="$(shell_quote "$REMOTE_DIR/third_party/rtl/nvdla")"
  unpack_command="$(inner_ssh_command \
    "mkdir -p $destination_quoted && tar -xf - -C $destination_quoted")"
  COPYFILE_DISABLE=1 tar -cf - \
    -C "$source" \
    .emuflow-source.json LICENSE README.md VERSION vmod |
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
openroad="$4"
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
test -x "$openroad"

/usr/bin/time -v -o "$root/phase3-time.txt" \
  env PYTHONPATH=src python3 -m emuflow phase3 \
    --ir "$ir" \
    --platform "$platform" \
    --out "$output" \
    --seed 20260727 \
    --provider tritonpart \
    --openroad "$openroad" \
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
  --provider tritonpart \
  --openroad "$openroad" \
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
    --provider tritonpart \
    --openroad "$openroad" \
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
  --provider tritonpart \
  --openroad "$openroad" \
  > "$connected_root/phase3-repeat-stdout.json"

connected_hash="$(
  sha256sum "$connected_root/phase3/assignment.json" | awk '{print $1}'
)"
connected_repeat_hash="$(
  sha256sum "$connected_root/phase3-repeat/assignment.json" | awk '{print $1}'
)"
test "$connected_hash" = "$connected_repeat_hash"

/usr/bin/time -v -o "$root/phase3-greedy-time.txt" \
  env PYTHONPATH=src python3 -m emuflow phase3 \
    --ir "$ir" \
    --platform "$platform" \
    --out "$root/phase3-greedy" \
    --seed 20260727 \
    --provider greedy \
    > "$root/phase3-greedy-stdout.json"

/usr/bin/time -v -o "$connected_root/phase3-greedy-time.txt" \
  env PYTHONPATH=src python3 -m emuflow phase3 \
    --ir "$connected_ir" \
    --platform "$platform" \
    --out "$connected_root/phase3-greedy" \
    --seed 20260727 \
    --provider greedy \
    > "$connected_root/phase3-greedy-stdout.json"

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

scale_greedy = json.loads(
    (root / "phase3-greedy/phase3_report.json").read_text()
)
connected_greedy = json.loads(
    (connected_root / "phase3-greedy/phase3_report.json").read_text()
)
comparison = {
    "schema": "emuflow.phase3-provider-comparison/v1",
    "designs": {
        "picorv32_x32": {
            "tritonpart": {
                "provider": scale_report["provider"],
                "validation": scale_report["validation"],
                "partition_cells": [
                    item["instance_count"] for item in scale_partitions
                ],
            },
            "greedy": {
                "provider": scale_greedy["provider"],
                "validation": scale_greedy["validation"],
                "partition_cells": [
                    item["instance_count"]
                    for item in scale_greedy["partitions"]
                ],
            },
        },
        "picorv32_connected": {
            "tritonpart": {
                "provider": connected_report["provider"],
                "validation": connected_report["validation"],
                "partition_cells": [
                    item["instance_count"] for item in connected_partitions
                ],
            },
            "greedy": {
                "provider": connected_greedy["provider"],
                "validation": connected_greedy["validation"],
                "partition_cells": [
                    item["instance_count"]
                    for item in connected_greedy["partitions"]
                ],
            },
        },
    },
}
(root / "phase3-provider-comparison.json").write_text(
    json.dumps(comparison, indent=2, sort_keys=True) + "\n"
)
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
    "$output/virtual_runtime_controller.sv" \
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
    "\$phase6/virtual_runtime_controller.sv" \
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
dut_period=10.0
runtime_xdc=""
if [ -s "$root/phase7c/runtime_timing.xdc" ]; then
  dut_period="$(
    python3 -c \
      'import json,sys; print(json.load(open(sys.argv[1]))["virtual_dut_clock"]["nominal_period_ns"])' \
      "$root/phase7c/runtime_contract.json"
  )"
  runtime_xdc="$root/phase7c/runtime_timing.xdc"
fi
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
  if [ -n "$runtime_xdc" ]; then
    /usr/bin/time -v -o "$target/vivado-time.txt" \
      "$vivado_root/bin/vivado" -mode batch -nojournal -nolog \
        -source scripts/vivado/validate_mapped.tcl \
        -tclargs xcvu3p-ffvc1517-2-e \
        "$target/mapped.v" \
        "picorv32__$fpga" \
        "$source/placement-openparf/placement.vivado.tsv" \
        "$target/vivado" \
        "$expected_cells" clk "$dut_period" "$runtime_xdc" \
        > "$target/vivado-validation.log" 2>&1
  else
    /usr/bin/time -v -o "$target/vivado-time.txt" \
      "$vivado_root/bin/vivado" -mode batch -nojournal -nolog \
        -source scripts/vivado/validate_mapped.tcl \
        -tclargs xcvu3p-ffvc1517-2-e \
        "$target/mapped.v" \
        "picorv32__$fpga" \
        "$source/placement-openparf/placement.vivado.tsv" \
        "$target/vivado" \
        "$expected_cells" clk "$dut_period" \
        > "$target/vivado-validation.log" 2>&1
  fi
  grep 'EMUFLOW_MAPPED_VIVADO status=pass' \
    "$target/vivado-validation.log"
  test -s "$target/vivado/routed.dcp"
done

python3 - <<'PY'
import json
from pathlib import Path

root = Path("build/remote/benchmarks/picorv32-l2/phase7b")
phase7a = root.parent / "phase7a"
total = 0
total_original = 0
total_transport = 0
for fpga in ("fpga0", "fpga1"):
    report = json.loads((root / fpga / "emission-report.json").read_text())
    lowering = json.loads(
        (phase7a / fpga / "lowering-report.json").read_text()
    )
    if report["status"] != "pass":
        raise SystemExit(f"{fpga} mapped Verilog emission failed")
    if not (root / fpga / "vivado/routed.dcp").is_file():
        raise SystemExit(f"{fpga} routed checkpoint is missing")
    if not (root / fpga / "vivado/route_status.rpt").is_file():
        raise SystemExit(f"{fpga} route status report is missing")
    total += report["instances"]
    total_original += report["instances"] - lowering["transport_instances"]
    total_transport += lowering["transport_instances"]
    print(
        "EMUFLOW_PICORV32_PHASE7B_FPGA "
        f"status=pass fpga={fpga} cells={report['instances']} "
        f"nets={report['nets']} ports={report['ports']}"
    )
if total_original != 3812:
    raise SystemExit(
        f"Phase 7B retained {total_original} original cells; expected 3812"
    )
if total != total_original + total_transport:
    raise SystemExit("Phase 7B transport cell accounting mismatch")
print(
    "EMUFLOW_PICORV32_PHASE7B "
    f"status=pass routed_cells={total} "
    f"original_cells={total_original} "
    f"transport_cells={total_transport}"
)
PY
REMOTE
}

picorv32_phase7c_prepare_remote() {
  remote_script <<'REMOTE'
set -eu
remote_dir="$1"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd "$remote_dir"

root=build/remote/benchmarks/picorv32-l2
output="$root/phase7c"
rm -rf "$output"
PYTHONPATH=src python3 -m emuflow phase7c \
  --schedule "$root/phase5/schedule.json" \
  --platform platforms/virtual/xcvu3p_2fpga_p2p.json \
  --phase3-report "$root/phase3/phase3_report.json" \
  --phase4-report "$root/phase4/phase4_report.json" \
  --phase5-report "$root/phase5/phase5_report.json" \
  --phase6-report "$root/phase6/phase6_report.json" \
  --simulation-frames 64 \
  --out "$output" \
  > "$root/phase7c-prepare-stdout.json"

/usr/bin/iverilog -g2012 -s virtual_runtime_controller_tb \
  -o "$output/virtual_runtime_controller_simv" \
  "$output/virtual_runtime_controller.sv" \
  "$output/virtual_runtime_controller_tb.sv"
/usr/bin/vvp "$output/virtual_runtime_controller_simv" \
  > "$output/virtual_runtime_controller_sim.log"
grep 'EMUFLOW_RUNTIME_TB status=pass' \
  "$output/virtual_runtime_controller_sim.log"

python3 - <<'PY'
import json
from pathlib import Path

root = Path("build/remote/benchmarks/picorv32-l2/phase7c")
report = json.loads((root / "phase7c_report.json").read_text())
runtime = json.loads((root / "runtime_contract.json").read_text())
if report["status"] != "generated":
    raise SystemExit("Phase 7C prepare did not generate a pending contract")
if runtime["frame"]["slots"] != 32:
    raise SystemExit("Phase 7C frame length mismatch")
if runtime["frame"]["completion_slot"] != 6:
    raise SystemExit("Phase 7C completion slot mismatch")
if runtime["frame"]["shadow_settle_slots"] != 25:
    raise SystemExit("Phase 7C shadow-settle margin mismatch")
if runtime["virtual_dut_clock"]["nominal_frequency_mhz"] != 7.8125:
    raise SystemExit("Phase 7C nominal virtual frequency mismatch")
print(
    "EMUFLOW_PICORV32_PHASE7C_PREPARE "
    "status=pass frame_slots=32 completion_slot=6 "
    "shadow_settle_slots=25 shadow_settle_ns=100 "
    "nominal_virtual_frequency_mhz=7.8125"
)
PY
REMOTE
}

picorv32_phase7c_finalize_remote() {
  remote_script <<'REMOTE'
set -eu
remote_dir="$1"
vivado_root="$2"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd "$remote_dir"

root=build/remote/benchmarks/picorv32-l2
phase7b="$root/phase7b"
phase7c="$root/phase7c"
test -s "$phase7c/runtime_timing.xdc"
for fpga in fpga0 fpga1; do
  expected_cells="$(
    python3 -c \
      'import json,sys; print(json.load(open(sys.argv[1]))["instances"])' \
      "$phase7b/$fpga/emission-report.json"
  )"
  "$vivado_root/bin/vivado" -mode batch -nojournal -nolog \
    -source scripts/vivado/report_runtime_contract.tcl \
    -tclargs "$phase7b/$fpga/vivado/routed.dcp" \
    "$phase7c/$fpga" "$expected_cells" \
    > "$phase7c/$fpga-vivado-runtime.log" 2>&1
  grep 'EMUFLOW_RUNTIME_VIVADO status=pass' \
    "$phase7c/$fpga-vivado-runtime.log"
done

python3 - <<'PY'
import hashlib
import json
from pathlib import Path

root = Path("build/remote/benchmarks/picorv32-l2")
records = []
for fpga in ("fpga0", "fpga1"):
    lowering = json.loads(
        (root / "phase7a" / fpga / "lowering-report.json").read_text()
    )
    emission = json.loads(
        (root / "phase7b" / fpga / "emission-report.json").read_text()
    )
    netlist = json.loads(
        (root / "phase6" / fpga / "netlist.json").read_text()
    )
    metrics = {}
    for line in (
        root / "phase7c" / fpga / "runtime_metrics.tsv"
    ).read_text().splitlines()[1:]:
        key, value = line.split("\t")
        metrics[key] = value
    checkpoint = root / "phase7b" / fpga / "vivado/routed.dcp"
    digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    original_cells = len(netlist["instances"])
    transport_cells = lowering["transport_instances"]
    if emission["instances"] != original_cells + transport_cells:
        raise SystemExit(f"{fpga} physical cell accounting mismatch")
    records.append(
        {
            "fpga": fpga,
            "part": "xcvu3p-ffvc1517-2-e",
            "original_cells": original_cells,
            "transport_cells": transport_cells,
            "routed_cells": int(metrics["cells"]),
            "nets": int(metrics["nets"]),
            "ports": int(metrics["ports"]),
            "unrouted_nets": int(metrics["unrouted_nets"]),
            "drc_violations": int(metrics["drc_violations"]),
            "wns_ns": float(metrics["wns_ns"]),
            "timing": {
                "dut_wns_ns": float(metrics["dut_wns_ns"]),
                "fabric_wns_ns": float(metrics["fabric_wns_ns"]),
                "fabric_to_dut_wns_ns": float(
                    metrics["fabric_to_dut_wns_ns"]
                ),
            },
            "clocks": {
                "fabric_period_ns": float(metrics["fabric_period_ns"]),
                "dut_period_ns": float(metrics["dut_period_ns"]),
            },
            "routed_dcp": {
                "bytes": checkpoint.stat().st_size,
                "sha256": digest,
            },
        }
    )
summary = {
    "schema": "emuflow.phase7b-physical-summary/v1",
    "status": "pass",
    "design": "picorv32",
    "platform": "virtual_xcvu3p_2fpga_p2p",
    "fpgas": records,
}
(root / "phase7c/physical_summary.json").write_text(
    json.dumps(summary, indent=2, sort_keys=True) + "\n"
)
PY

PYTHONPATH=src python3 -m emuflow phase7c \
  --schedule "$root/phase5/schedule.json" \
  --platform platforms/virtual/xcvu3p_2fpga_p2p.json \
  --phase3-report "$root/phase3/phase3_report.json" \
  --phase4-report "$root/phase4/phase4_report.json" \
  --phase5-report "$root/phase5/phase5_report.json" \
  --phase6-report "$root/phase6/phase6_report.json" \
  --physical-summary "$phase7c/physical_summary.json" \
  --simulation-frames 64 \
  --out "$phase7c" \
  > "$root/phase7c-final-stdout.json"

python3 - <<'PY'
import json
from pathlib import Path

root = Path("build/remote/benchmarks/picorv32-l2/phase7c")
report = json.loads((root / "phase7c_report.json").read_text())
qor = json.loads((root / "qor_report.json").read_text())
if report["status"] != "pass" or qor["status"] != "pass":
    raise SystemExit("Phase 7C final report did not pass")
physical = qor["physical"]
if physical["original_cells"] != 3812:
    raise SystemExit("Phase 7C lost original design cells")
if physical["routed_cells"] != (
    physical["original_cells"] + physical["transport_cells"]
):
    raise SystemExit("Phase 7C QoR cell accounting mismatch")
print(
    "EMUFLOW_PICORV32_PHASE7C "
    f"status=pass routed_cells={physical['routed_cells']} "
    f"original_cells={physical['original_cells']} "
    f"transport_cells={physical['transport_cells']} "
    f"unrouted_nets={physical['unrouted_nets']} "
    f"drc_violations={physical['drc_violations']} "
    f"worst_wns_ns={physical['worst_wns_ns']}"
)
PY

repeat="$root/phase7c-repeat"
rm -rf "$repeat"
PYTHONPATH=src python3 -m emuflow phase7c \
  --schedule "$root/phase5/schedule.json" \
  --platform platforms/virtual/xcvu3p_2fpga_p2p.json \
  --phase3-report "$root/phase3/phase3_report.json" \
  --phase4-report "$root/phase4/phase4_report.json" \
  --phase5-report "$root/phase5/phase5_report.json" \
  --phase6-report "$root/phase6/phase6_report.json" \
  --simulation-frames 64 \
  --out "$repeat" \
  > "$root/phase7c-repeat-stdout.json"
for artifact in \
  runtime_contract.json \
  runtime_timing.xdc \
  virtual_runtime_controller.sv \
  virtual_runtime_controller_tb.sv; do
  cmp "$phase7c/$artifact" "$repeat/$artifact"
done
runtime_hash="$(
  sha256sum \
    "$phase7c/runtime_contract.json" \
    "$phase7c/runtime_timing.xdc" \
    "$phase7c/virtual_runtime_controller.sv" \
    "$phase7c/virtual_runtime_controller_tb.sv" |
  awk '{print $1}' | sha256sum | awk '{print $1}'
)"
printf 'phase7c_runtime_artifact_set_sha256=%s\n' "$runtime_hash"
REMOTE
}

picorv32_phase7c_remote() {
  picorv32_phase7c_prepare_remote
  picorv32_phase7b_remote
  picorv32_phase7c_finalize_remote
}

picorv32_phase7d_remote() {
  remote_script <<'REMOTE'
set -eu
remote_dir="$1"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd "$remote_dir"

root=build/remote/benchmarks/picorv32-l2
source_commit="$(cat .emuflow-source-commit)"
output="$root/phase7d"
repeat="$root/phase7d-repeat"
rm -rf "$output" "$repeat"

run_audit() {
  target="$1"
  stdout="$2"
  PYTHONPATH=src python3 -m emuflow phase7d \
    --benchmark-report "$root/benchmark_report.json" \
    --phase3-report "$root/phase3/phase3_report.json" \
    --phase4-report "$root/phase4/phase4_report.json" \
    --phase5-report "$root/phase5/phase5_report.json" \
    --phase6-report "$root/phase6/phase6_report.json" \
    --phase7c-report "$root/phase7c/phase7c_report.json" \
    --runtime-contract "$root/phase7c/runtime_contract.json" \
    --qor-report "$root/phase7c/qor_report.json" \
    --physical-summary "$root/phase7c/physical_summary.json" \
    --platform platforms/virtual/xcvu3p_2fpga_p2p.json \
    --lowering-report \
      "fpga0=$root/phase7a/fpga0/lowering-report.json" \
    --lowering-report \
      "fpga1=$root/phase7a/fpga1/lowering-report.json" \
    --placement-report \
      "fpga0=$root/phase7a/fpga0/placement-openparf/phase2_report.json" \
    --placement-report \
      "fpga1=$root/phase7a/fpga1/placement-openparf/phase2_report.json" \
    --emission-report \
      "fpga0=$root/phase7b/fpga0/emission-report.json" \
    --emission-report \
      "fpga1=$root/phase7b/fpga1/emission-report.json" \
    --artifact "synthesis.mapped_json=$root/synthesis/mapped.json" \
    --artifact "global.emuir=$root/phase1/design.emuir.json" \
    --artifact "partition.assignment=$root/phase3/assignment.json" \
    --artifact "system.routes=$root/phase4/routes.json" \
    --artifact "system.schedule=$root/phase5/schedule.json" \
    --artifact "system.lane_map=$root/phase6/lane_map.json" \
    --artifact "fpga0.netlist=$root/phase6/fpga0/netlist.json" \
    --artifact "fpga1.netlist=$root/phase6/fpga1/netlist.json" \
    --artifact \
      "fpga0.placement=$root/phase7a/fpga0/placement-openparf/placement.json" \
    --artifact \
      "fpga1.placement=$root/phase7a/fpga1/placement-openparf/placement.json" \
    --artifact "fpga0.mapped_verilog=$root/phase7b/fpga0/mapped.v" \
    --artifact "fpga1.mapped_verilog=$root/phase7b/fpga1/mapped.v" \
    --artifact "fpga0.routed_dcp=$root/phase7b/fpga0/vivado/routed.dcp" \
    --artifact "fpga1.routed_dcp=$root/phase7b/fpga1/vivado/routed.dcp" \
    --artifact \
      "runtime.contract=$root/phase7c/runtime_contract.json" \
    --artifact "runtime.timing_xdc=$root/phase7c/runtime_timing.xdc" \
    --artifact \
      "runtime.physical_summary=$root/phase7c/physical_summary.json" \
    --artifact "release.qor=$root/phase7c/qor_report.json" \
    --source-commit "$source_commit" \
    --out "$target" \
    > "$stdout"
}

run_audit "$output" "$root/phase7d-stdout.json"
run_audit "$repeat" "$root/phase7d-repeat-stdout.json"
cmp "$output/release_manifest.json" "$repeat/release_manifest.json"
manifest_hash="$(
  sha256sum "$output/release_manifest.json" | awk '{print $1}'
)"

python3 - <<'PY'
import json
from pathlib import Path

root = Path("build/remote/benchmarks/picorv32-l2/phase7d")
manifest = json.loads((root / "release_manifest.json").read_text())
report = json.loads((root / "phase7d_report.json").read_text())
if manifest["status"] != "pass" or report["status"] != "pass":
    raise SystemExit("Phase 7D release audit did not pass")
if set(manifest["gates"]) != {f"G{index}" for index in range(10)}:
    raise SystemExit("Phase 7D did not cover exactly G0-G9")
if any(
    gate["status"] != "pass" for gate in manifest["gates"].values()
):
    raise SystemExit("Phase 7D contains a failing gate")
metrics = manifest["metrics"]
if metrics["original_cells"] != 3812:
    raise SystemExit("Phase 7D original cell count mismatch")
if metrics["routed_cells"] != 4223:
    raise SystemExit("Phase 7D routed cell count mismatch")
if len(manifest["artifacts"]) != 18:
    raise SystemExit("Phase 7D artifact inventory count mismatch")
print(
    "EMUFLOW_PICORV32_PHASE7D "
    f"status=pass gates={len(manifest['gates'])} "
    f"source_files={metrics['source_files']} "
    f"artifacts={len(manifest['artifacts'])} "
    f"original_cells={metrics['original_cells']} "
    f"transport_cells={metrics['transport_cells']} "
    f"routed_cells={metrics['routed_cells']} "
    f"cut_nets={metrics['cut_nets']} "
    f"scheduled_bit_hops={metrics['scheduled_bit_hops']} "
    f"equivalence_cycles={metrics['equivalence_cycles']} "
    f"worst_wns_ns={metrics['worst_wns_ns']}"
)
PY
printf 'phase7d_release_manifest_sha256=%s\n' "$manifest_hash"
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

veer_eh1_screen_remote() {
  remote_script <<'REMOTE'
set -eu
remote_dir="$1"
vivado_root="$2"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd "$remote_dir"

source_root=third_party/rtl/veer_eh1
output=build/remote/benchmarks/veer-eh1-vivado-screen
vivado="$vivado_root/bin/vivado"
test -x "$vivado"
test -x "$source_root/configs/veer.config"
test -f scripts/vivado/synth_veer_eh1.tcl

(
  cd "$source_root"
  RV_ROOT="$PWD" ./configs/veer.config \
    -unset=assert_on -set=fpga_optimize=1
)
config_root="$source_root/snapshots/default"
test -s "$config_root/common_defines.vh"
mkdir -p "$output"

/usr/bin/time -v -o "$output/time.txt" \
  "$vivado" -mode batch -nojournal -nolog \
    -source scripts/vivado/synth_veer_eh1.tcl \
    -tclargs \
      "$source_root" \
      "$config_root" \
      "$output" \
      xcvu3p-ffvc1517-2-e \
    > "$output/vivado.log" 2>&1

test -s "$output/primitive_counts.tsv"
cat "$output/primitive_counts.tsv"
REMOTE
}

nvdla_screen_remote() {
  remote_script <<'REMOTE'
set -eu
remote_dir="$1"
vivado_root="$2"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd "$remote_dir"

source_root=third_party/rtl/nvdla
output=build/remote/benchmarks/nvdla-nvdlav1
vivado="$vivado_root/bin/vivado"
test -x "$vivado"
test -f "$source_root/.emuflow-source.json"
test -f "$source_root/vmod/nvdla/top/NV_nvdla.v"
test -f scripts/vivado/synth_nvdla.tcl

mkdir -p "$output"
python3 scripts/benchmarks/nvdla_ram_stubs.py \
  "$source_root/vmod/rams/synth" \
  "$output/nvdla_ram_blackboxes.v" \
  > "$output/ram_stubs.log"

/usr/bin/time -v -o "$output/vivado_time.txt" \
  "$vivado" -mode batch -nojournal -nolog \
    -source scripts/vivado/synth_nvdla.tcl \
    -tclargs \
      "$source_root" \
      "$output/nvdla_ram_blackboxes.v" \
      "$output" \
      xcvu3p-ffvc1517-2-e \
    > "$output/vivado.log" 2>&1

test -s "$output/nvdla_synth.dcp"
test -s "$output/nvdla_synth.edf"
test -s "$output/nvdla_synth.v"
test -s "$output/primitive_counts.tsv"
cells="$(awk -F '\t' '$1 == "all_cells" {print $2}' \
  "$output/primitive_counts.tsv")"
blackboxes="$(awk -F '\t' '$1 == "blackbox_cells" {print $2}' \
  "$output/primitive_counts.tsv")"
if [ -z "$cells" ] || [ "$cells" -lt 100000 ]; then
  echo "error: NVDLA Vivado netlist has only ${cells:-unknown} cells" >&2
  exit 1
fi
printf \
  'EMUFLOW_NVDLA_SCREEN status=pass cells=%s blackbox_cells=%s dcp=%s\n' \
  "$cells" "$blackboxes" "$output/nvdla_synth.dcp"
REMOTE
}

nvdla_partition_a_synth_remote() {
  remote_script <<'REMOTE'
set -eu
remote_dir="$1"
vivado_root="$2"
yosys_path="$3"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd "$remote_dir"

source_root=third_party/rtl/nvdla
root=build/remote/benchmarks/nvdla-partition-a-flow
synthesis="$root/synthesis"
vivado="$vivado_root/bin/vivado"
top=NV_NVDLA_partition_a
mapped_top=NV_NVDLA_partition_a
platform=platforms/virtual/xcvu3p_4fpga_mesh.json
test -x "$vivado"
test -x "$yosys_path"
test -f "$source_root/.emuflow-source.json"
test -f scripts/vivado/synth_nvdla.tcl
test -f scripts/yosys/xilinx_softlogic_map.v
test -f scripts/yosys/xilinx_ultrascale_softlogic_cells.v
test -s "$platform"
rm -rf "$root"
mkdir -p "$synthesis"

python3 scripts/benchmarks/nvdla_ram_stubs.py \
  "$source_root/vmod/rams/synth" \
  "$synthesis/nvdla_ram_wrappers.v" \
  --register-model-pattern 'nv_ram_rws_32x(512|544|768)' \
  > "$synthesis/ram_models.log"
grep 'register_models=3' "$synthesis/ram_models.log"

/usr/bin/time -v -o "$synthesis/vivado-time.txt" \
  "$vivado" -mode batch -nojournal -nolog \
    -source scripts/vivado/synth_nvdla.tcl \
    -tclargs \
      "$source_root" \
      "$synthesis/nvdla_ram_wrappers.v" \
      "$synthesis" \
      xcvu3p-ffvc1517-2-e \
      "$top" \
      on \
    > "$synthesis/vivado.log" 2>&1

netlist="$synthesis/nv_nvdla_partition_a_synth.v"
test -s "$netlist"
test -s "$synthesis/nv_nvdla_partition_a_synth.dcp"
blackboxes="$(
  awk -F '\t' '$1 == "blackbox_cells" {print $2}' \
    "$synthesis/primitive_counts.tsv"
)"
test "$blackboxes" = 0

yosys_script="$synthesis/import-vivado.ys"
{
  printf 'read_verilog -lib scripts/yosys/xilinx_ultrascale_softlogic_cells.v\n'
  # Vivado appends a simulation-only glbl module containing drive-strength
  # syntax that is irrelevant to synthesis and unsupported by Yosys.
  printf 'read_verilog -DGLBL -sv %s\n' "$netlist"
  printf 'hierarchy -check -top %s\n' "$mapped_top"
  printf 'flatten\n'
  printf 'techmap -map scripts/yosys/xilinx_softlogic_map.v\n'
  printf 'opt_clean\n'
  printf 'check\n'
  printf 'setattr -set KEEP \"yes\" c:*\n'
  printf 'setattr -set DONT_TOUCH \"yes\" c:*\n'
  printf 'write_json %s\n' "$synthesis/mapped.json"
  printf 'write_verilog -norename %s\n' "$synthesis/mapped.v"
} > "$yosys_script"

/usr/bin/time -v -o "$synthesis/yosys-time.txt" \
  "$yosys_path" -s "$yosys_script" \
  > "$synthesis/yosys.log" 2>&1
test -s "$synthesis/mapped.json"
test -s "$synthesis/mapped.v"

/usr/bin/time -v -o "$root/phase1-time.txt" \
  env PYTHONPATH=src python3 -m emuflow phase1 \
    --yosys-json "$synthesis/mapped.json" \
    --top "$mapped_top" \
    --clock nvdla_core_clk \
    --platform "$platform" \
    --require-no-fabric-clock \
    --out "$root/phase1" \
    > "$root/phase1-stdout.json"

python3 - <<'PY'
import json
from collections import Counter
from pathlib import Path

root = Path("build/remote/benchmarks/nvdla-partition-a-flow")
counts = {}
with (root / "synthesis/primitive_counts.tsv").open() as stream:
    next(stream)
    for line in stream:
        key, value = line.rstrip().split("\t")
        counts[key] = int(value)
if counts["blackbox_cells"] != 0:
    raise SystemExit("NVDLA partition A still contains black boxes")

design = json.loads((root / "phase1/design.emuir.json").read_text())
cell_types = Counter(instance["type"] for instance in design["instances"])
unsupported = sorted(
    cell_type
    for cell_type in cell_types
    if not (
        cell_type.startswith("LUT")
        or cell_type in {"FDCE", "FDPE", "FDRE", "FDSE"}
    )
)
if unsupported:
    raise SystemExit(f"unsupported mapped cell types: {unsupported}")
if len(design["instances"]) < 300_000:
    raise SystemExit("mapped NVDLA partition A is below the scale gate")
print(
    "EMUFLOW_NVDLA_PARTITION_A_SYNTH "
    f"status=pass vivado_cells={counts['primitive_cells']} "
    f"blackboxes={counts['blackbox_cells']} "
    f"emuir_cells={len(design['instances'])} "
    f"nets={len(design['nets'])} "
    f"types={json.dumps(cell_types, sort_keys=True)}"
)
PY
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

build_openparf_sparse_remote() {
  local remote_root_quoted
  remote_root_quoted="$(shell_quote "$OPENPARF_REMOTE_ROOT")"
  remote_script <<REMOTE
set -eu
remote_dir="\$1"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
openparf_root=$remote_root_quoted
cd "\$remote_dir"
scripts/openparf/build_sparse_legalizer.sh \
  "\$openparf_root/OpenPARF-src" \
  "\$openparf_root/OpenPARF-build" \
  "\$openparf_root/OpenPARF-install" \
  "\$openparf_root/OpenPARF-install-greedy-first"
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
  tritonpart-bootstrap)
    tritonpart_bootstrap
    ;;
  repart-bootstrap)
    repart_bootstrap
    ;;
  repart-phase3-smoke)
    repart_phase3_smoke_remote
    ;;
  repart-phase3-picorv32)
    repart_phase3_picorv32_remote
    ;;
  repart-phase3-nvdla)
    repart_phase3_nvdla_remote
    ;;
  repart-nvdla-downstream)
    repart_nvdla_downstream_remote
    ;;
  repart-nvdla-phase7a)
    repart_nvdla_physical_remote phase7a
    ;;
  repart-nvdla-phase7b)
    repart_nvdla_physical_remote phase7b
    ;;
  repart-nvdla-phase7c-finalize)
    repart_nvdla_physical_remote phase7c-finalize
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
  nvdla-arch-full)
    nvdla_arch_full_remote
    ;;
  nvdla-arch-vu9p-full)
    nvdla_arch_vu9p_full_remote
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
  picorv32-phase7c)
    picorv32_phase7c_remote
    ;;
  picorv32-phase7c-finalize)
    picorv32_phase7c_finalize_remote
    ;;
  picorv32-phase7c-all)
    picorv32_phase6_remote
    picorv32_phase7a_remote
    picorv32_phase7c_remote
    ;;
  picorv32-phase7d)
    picorv32_phase7d_remote
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
  veer-eh1-sync)
    sync_veer_eh1_source
    ;;
  veer-eh1-screen)
    veer_eh1_screen_remote
    ;;
  nvdla-sync)
    sync_nvdla_source
    ;;
  nvdla-screen)
    nvdla_screen_remote
    ;;
  nvdla-partition-a-synth)
    nvdla_partition_a_synth_remote
    ;;
  openparf-sync)
    sync_openparf
    ;;
  openparf-build)
    build_openparf_remote
    ;;
  openparf-build-sparse)
    build_openparf_sparse_remote
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
