#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  nvdla_partition_a_balanced.sh logical ROOT PHASE1_IR
  nvdla_partition_a_balanced.sh phase7a ROOT
  nvdla_partition_a_balanced.sh phase7b ROOT
  nvdla_partition_a_balanced.sh phase7c-finalize ROOT
  nvdla_partition_a_balanced.sh phase7d ROOT

Reproduce the balanced four-VU9P NVDLA partition-A flow on proj169-2.

The logical command starts from an existing Phase 1 EmuIR, runs the real
OpenROAD/TritonPart provider, independently legalizes any best-effort result
against EmuFlow's multi-resource upper bounds, and executes Phases 4-6 plus
the initial Phase 7C runtime contract.

The phase7a command synthesizes transport RTL, lowers each FPGA placement IR,
and runs OpenPARF. It expects the logical command's ROOT layout.
The phase7b command emits structural netlists and runs Vivado with sparse
OpenPARF anchors on all four balanced partitions.

Environment overrides:
  EMUFLOW_REPO
  EMUFLOW_OPENROAD
  EMUFLOW_PHASE7A_ORDER
  EMUFLOW_GLOBAL_PLACE_FPGAS
  EMUFLOW_EQUIVALENCE_CYCLES
  EMUFLOW_NVDLA_SYNTHESIS_DIR
                      Original checked NVDLA synthesis directory containing
                      mapped.json; required by phase7d unless ROOT/synthesis
                      already exists.
EOF
}

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
  usage >&2
  exit 2
fi

command_name="$1"
root="$(mkdir -p "$2" && realpath "$2")"
repo="${EMUFLOW_REPO:-/home/ziyiwang21/work/FGPA_emulation}"
openroad="${EMUFLOW_OPENROAD:-/home/ziyiwang21/work/tools/openroad-2.0-17598-ga008522d8/bin/openroad}"
phase7a_order="${EMUFLOW_PHASE7A_ORDER:-fpga3 fpga2 fpga0 fpga1}"
global_place_fpgas="${EMUFLOW_GLOBAL_PLACE_FPGAS:-fpga0 fpga1 fpga2 fpga3}"
equivalence_cycles="${EMUFLOW_EQUIVALENCE_CYCLES:-2}"
platform="$repo/platforms/virtual/xcvu9p_4fpga_mesh.json"
seed=20260727

require_file() {
  if [ ! -s "$1" ]; then
    echo "required artifact is missing or empty: $1" >&2
    exit 1
  fi
}

run_logical() {
  if [ "$#" -ne 1 ]; then
    usage >&2
    exit 2
  fi
  local phase1_ir
  phase1_ir="$(realpath "$1")"
  require_file "$phase1_ir"
  require_file "$platform"
  require_file "$openroad"

  mkdir -p "$root/phase1"
  ln -sfn "$phase1_ir" "$root/phase1/design.emuir.json"

  /usr/bin/time -v -o "$root/phase3-time.txt" \
    env PYTHONPATH="$repo/src" python3 -m emuflow phase3 \
      --ir "$root/phase1/design.emuir.json" \
      --platform "$platform" \
      --out "$root/phase3" \
      --provider tritonpart \
      --openroad "$openroad" \
      --seed "$seed" \
      --min-used-fpgas 4 \
      --balance-tolerance 0.10 \
      --tritonpart-repair-balance \
      > "$root/phase3-stdout.json"

  env PYTHONPATH="$repo/src" python3 -m emuflow partition validate \
    "$root/phase3/assignment.json" \
    --clusters "$root/phase3/clusters.json" \
    --ir "$root/phase1/design.emuir.json" \
    --platform "$platform" \
    > "$root/phase3-independent-validation.json"

  /usr/bin/time -v -o "$root/phase4-time.txt" \
    env PYTHONPATH="$repo/src" python3 -m emuflow phase4 \
      --assignment "$root/phase3/assignment.json" \
      --platform "$platform" \
      --frame-slots 4096 \
      --out "$root/phase4" \
      > "$root/phase4-stdout.json"

  /usr/bin/time -v -o "$root/phase5-time.txt" \
    env PYTHONPATH="$repo/src" python3 -m emuflow phase5 \
      --routes "$root/phase4/routes.json" \
      --platform "$platform" \
      --simulation-frames 16 \
      --out "$root/phase5" \
      > "$root/phase5-stdout.json"

  /usr/bin/time -v -o "$root/phase6-time.txt" \
    env PYTHONPATH="$repo/src" python3 -m emuflow phase6 \
      --ir "$root/phase1/design.emuir.json" \
      --assignment "$root/phase3/assignment.json" \
      --schedule "$root/phase5/schedule.json" \
      --platform "$platform" \
      --equivalence-cycles "$equivalence_cycles" \
      --equivalence-seed "$seed" \
      --out "$root/phase6" \
      > "$root/phase6-stdout.json"

  env PYTHONPATH="$repo/src" python3 -m emuflow split validate \
    "$root/phase6/manifest.json" \
    --ir "$root/phase1/design.emuir.json" \
    --assignment "$root/phase3/assignment.json" \
    --schedule "$root/phase5/schedule.json" \
    --platform "$platform" \
    > "$root/phase6-independent-validation.json"

  mkdir -p "$root/phase7c"
  env PYTHONPATH="$repo/src" python3 -m emuflow phase7c \
    --schedule "$root/phase5/schedule.json" \
    --platform "$platform" \
    --phase3-report "$root/phase3/phase3_report.json" \
    --phase4-report "$root/phase4/phase4_report.json" \
    --phase5-report "$root/phase5/phase5_report.json" \
    --phase6-report "$root/phase6/phase6_report.json" \
    --simulation-frames 64 \
    --out "$root/phase7c" \
    > "$root/phase7c-initial-stdout.json"
}

run_phase7a() {
  require_file "$root/phase6/manifest.json"
  require_file "$repo/scripts/remote/nvdla_partition_a_phase7.sh"
  EMUFLOW_REPO="$repo" \
  EMUFLOW_FPGAS="$phase7a_order" \
  EMUFLOW_GLOBAL_PLACE_FPGAS="$global_place_fpgas" \
    "$repo/scripts/remote/nvdla_partition_a_phase7.sh" phase7a "$root"
}

run_phase7b() {
  require_file "$root/phase7c/runtime_timing.xdc"
  require_file "$repo/scripts/remote/nvdla_partition_a_phase7.sh"
  EMUFLOW_REPO="$repo" \
  EMUFLOW_FPGAS="$phase7a_order" \
  EMUFLOW_SPARSE_ANCHOR_FPGAS="fpga0 fpga1 fpga2 fpga3" \
    "$repo/scripts/remote/nvdla_partition_a_phase7.sh" phase7b "$root"
}

run_phase7c_finalize() {
  require_file "$root/phase7c/runtime_contract.json"
  require_file "$repo/scripts/remote/nvdla_partition_a_phase7.sh"
  EMUFLOW_REPO="$repo" \
    "$repo/scripts/remote/nvdla_partition_a_phase7.sh" \
      phase7c-finalize "$root"
}

run_phase7d() {
  local synthesis_dir="${EMUFLOW_NVDLA_SYNTHESIS_DIR:-}"
  require_file "$root/phase7c/phase7c_report.json"
  require_file "$repo/scripts/remote/nvdla_partition_a_phase7.sh"
  if [ ! -s "$root/synthesis/mapped.json" ]; then
    if [ -z "$synthesis_dir" ]; then
      echo "ROOT/synthesis/mapped.json is missing; set EMUFLOW_NVDLA_SYNTHESIS_DIR" >&2
      exit 1
    fi
    synthesis_dir="$(realpath "$synthesis_dir")"
    require_file "$synthesis_dir/mapped.json"
    ln -sfn "$synthesis_dir" "$root/synthesis"
  fi
  EMUFLOW_REPO="$repo" \
  EMUFLOW_PHASE7D_PROFILE=balanced \
    "$repo/scripts/remote/nvdla_partition_a_phase7.sh" phase7d "$root"
}

case "$command_name" in
  logical)
    if [ "$#" -ne 3 ]; then
      usage >&2
      exit 2
    fi
    run_logical "$3"
    ;;
  phase7a)
    if [ "$#" -ne 2 ]; then
      usage >&2
      exit 2
    fi
    run_phase7a
    ;;
  phase7b)
    if [ "$#" -ne 2 ]; then
      usage >&2
      exit 2
    fi
    run_phase7b
    ;;
  phase7c-finalize)
    if [ "$#" -ne 2 ]; then
      usage >&2
      exit 2
    fi
    run_phase7c_finalize
    ;;
  phase7d)
    if [ "$#" -ne 2 ]; then
      usage >&2
      exit 2
    fi
    run_phase7d
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
