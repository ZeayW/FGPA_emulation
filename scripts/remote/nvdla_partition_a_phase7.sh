#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  nvdla_partition_a_phase7.sh phase7a ROOT
  nvdla_partition_a_phase7.sh phase7b ROOT
  nvdla_partition_a_phase7.sh phase7c-finalize ROOT
  nvdla_partition_a_phase7.sh phase7d ROOT

Run the per-FPGA physical stages for an existing NVDLA partition-A Phase 6
result on proj169-2. ROOT must contain phase3 through phase6 artifacts.
The phase7c-finalize command additionally requires the four routed Phase 7B
checkpoints and converts their route, DRC, and timing results into the final
Phase 7C physical/QoR report.
The phase7d command rehashes the complete pinned source dependency set,
cross-checks G0-G9, hashes the release-critical artifacts, and requires a
byte-reproducible release manifest.

Environment overrides:
  EMUFLOW_REPO
  EMUFLOW_ARCH
  EMUFLOW_OPENPARF_ROOT
  EMUFLOW_OPENPARF_PYTHON
  EMUFLOW_YOSYS
  EMUFLOW_VIVADO
  EMUFLOW_PART
  EMUFLOW_FPGAS       Space-separated partition list for a resumed run.
  EMUFLOW_RESUME      Set to 1 to reuse checked lowering/reference artifacts.
  EMUFLOW_GLOBAL_PLACE_FPGAS
                      Space-separated FPGA list using OpenPARF global
                      coordinates plus ArchitectureDB legalization.
  EMUFLOW_SPARSE_ANCHOR_FPGAS
                      Space-separated FPGA list using sparse OpenPARF anchors
                      during Vivado implementation.
  EMUFLOW_MAIN_ANCHOR_MODULUS
                      Fix one LUT on every Nth deterministic OpenPARF site.
  EMUFLOW_MAIN_PLACE_DIRECTIVE
  EMUFLOW_MAIN_ROUTE_DIRECTIVE
  EMUFLOW_MAIN_UNPLACED_DCP
                      Reuse a checked fpga0 post-synthesis checkpoint.
EOF
}

if [ "$#" -ne 2 ]; then
  usage >&2
  exit 2
fi

command_name="$1"
root="$(realpath "$2")"
repo="${EMUFLOW_REPO:-/home/ziyiwang21/work/FGPA_emulation}"
arch="${EMUFLOW_ARCH:-/tmp/ziyiwang21-emuflow/nvdla-vu9p-arch/xcvu9p.arch.json}"
openparf_root="${EMUFLOW_OPENPARF_ROOT:-/home/ziyiwang21/work/tools}"
openparf_python="${EMUFLOW_OPENPARF_PYTHON:-/home/ziyiwang21/anaconda3/envs/deepgate/bin/python}"
yosys="${EMUFLOW_YOSYS:-/data/zhpei/oss-cad-suite/bin/yosys}"
vivado="${EMUFLOW_VIVADO:-/data2/vivado/2025.2/Vivado/bin/vivado}"
part="${EMUFLOW_PART:-xcvu9p-flga2104-2L-e}"
fpgas="${EMUFLOW_FPGAS:-fpga1 fpga2 fpga3 fpga0}"
resume="${EMUFLOW_RESUME:-0}"
global_place_fpgas="${EMUFLOW_GLOBAL_PLACE_FPGAS:-fpga0}"
sparse_anchor_fpgas="${EMUFLOW_SPARSE_ANCHOR_FPGAS:-fpga0}"
main_anchor_modulus="${EMUFLOW_MAIN_ANCHOR_MODULUS:-64}"
main_place_directive="${EMUFLOW_MAIN_PLACE_DIRECTIVE:-SSI_SpreadLogic_high}"
main_route_directive="${EMUFLOW_MAIN_ROUTE_DIRECTIVE:-Default}"
main_unplaced_dcp="${EMUFLOW_MAIN_UNPLACED_DCP:-}"

phase6="$root/phase6"
phase7a="$root/phase7a"
phase7b="$root/phase7b"
phase7c="$root/phase7c"

require_file() {
  if [ ! -s "$1" ]; then
    echo "required artifact is missing or empty: $1" >&2
    exit 1
  fi
}

list_contains() {
  local values="$1"
  local expected="$2"
  case " $values " in
    *" $expected "*)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

phase7a_one() {
  local fpga="$1"
  local target="$phase7a/$fpga"
  local reference="$target/placement-reference"
  local result

  mkdir -p "$target"
  if [ "$resume" != 1 ] ||
    [ ! -s "$target/transport.mapped.json" ] ||
    [ ! -s "$target/transport.emuir.json" ] ||
    [ ! -s "$target/transport-synthesis-report.json" ] ||
    [ ! -s "$target/transport-import-report.json" ]; then
    /usr/bin/time -v -o "$target/transport-synthesis-time.txt" \
      env PYTHONPATH="$repo/src" python3 -m emuflow synth-yosys \
        "$phase6/virtual_runtime_controller.sv" \
        "$phase6/$fpga/transport_schedule.sv" \
        --top "emuflow_transport_$fpga" \
        --family xcup \
        --policy logic-only \
        --yosys "$yosys" \
        --output "$target/transport.mapped.json" \
        --verilog-output "$target/transport.mapped.v" \
        --log "$target/transport-yosys.log" \
        > "$target/transport-synthesis-report.json"
    env PYTHONPATH="$repo/src" python3 -m emuflow import-yosys \
      "$target/transport.mapped.json" \
      --top "emuflow_transport_$fpga" \
      --clock fabric_clk \
      --output "$target/transport.emuir.json" \
      > "$target/transport-import-report.json"
  fi

  if [ "$resume" != 1 ] ||
    [ ! -s "$target/placement.emuir.json" ] ||
    [ ! -s "$target/lowering-report.json" ]; then
    require_file "$target/transport.emuir.json"
    /usr/bin/time -v -o "$target/lowering-time.txt" \
      env PYTHONPATH="$repo/src" python3 -m emuflow lower-placement-ir \
        --netlist "$phase6/$fpga/netlist.json" \
        --transport "$phase6/$fpga/transport.json" \
        --transport-ir "$target/transport.emuir.json" \
        --output "$target/placement.emuir.json" \
        --report "$target/lowering-report.json" \
        > "$target/lowering-stdout.json"
  fi

  if [ "$resume" != 1 ] ||
    [ ! -s "$reference/phase2_report.json" ] ||
    [ ! -s "$reference/openparf/openparf.json" ]; then
    /usr/bin/time -v -o "$target/placement-reference-time.txt" \
      env PYTHONPATH="$repo/src" python3 -m emuflow phase2 \
        --ir "$target/placement.emuir.json" \
        --arch "$arch" \
        --out "$reference" \
        > "$target/placement-reference-report.json"
  fi

  if list_contains "$global_place_fpgas" "$fpga"; then
    python3 - "$reference/openparf/openparf.json" \
      "$target/openparf-global.json" <<'PY'
import json
import sys
from pathlib import Path

source = Path(sys.argv[1])
output = Path(sys.argv[2])
config = json.loads(source.read_text(encoding="utf-8"))
config["legalize_flag"] = 0
config["detailed_place_flag"] = 0
config["result_dir"] = str(source.parent / "results-global")
output.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")
PY
    /usr/bin/time -v -o "$target/openparf-time.txt" \
      env CUDA_VISIBLE_DEVICES="" \
        PYTHONPATH="$repo/scripts/openparf/shims:$openparf_root/OpenPARF-install" \
        "$openparf_python" "$openparf_root/OpenPARF-install/openparf.py" \
          --config "$target/openparf-global.json" \
          --log "$target/openparf-global.log"
    result="$reference/openparf/results-global/NV_NVDLA_partition_a__${fpga}.pl"
    require_file "$result"
    /usr/bin/time -v -o "$target/architecture-legalization-time.txt" \
      env PYTHONPATH="$repo/src" python3 -m emuflow phase2 \
        --ir "$target/placement.emuir.json" \
        --arch "$arch" \
        --openparf-result "$result" \
        --openparf-global-result \
        --site-utilization-limit 0.75 \
        --out "$target/placement-openparf" \
        > "$target/placement-openparf-report.json"
  else
    /usr/bin/time -v -o "$target/openparf-time.txt" \
      env CUDA_VISIBLE_DEVICES="" \
        PYTHONPATH="$repo/scripts/openparf/shims:$openparf_root/OpenPARF-install" \
        "$openparf_python" "$openparf_root/OpenPARF-install/openparf.py" \
          --config "$reference/openparf/openparf.json" \
          --log "$target/openparf.log"
    result="$reference/openparf/results/NV_NVDLA_partition_a__${fpga}.pl"
    require_file "$result"
    env PYTHONPATH="$repo/src" python3 -m emuflow phase2 \
      --ir "$target/placement.emuir.json" \
      --arch "$arch" \
      --openparf-result "$result" \
      --out "$target/placement-openparf" \
      > "$target/placement-openparf-report.json"
  fi
}

run_phase7a() {
  require_file "$phase6/manifest.json"
  require_file "$arch"
  require_file "$openparf_root/OpenPARF-install/openparf.py"
  require_file "$yosys"
  mkdir -p "$phase7a"

  # The caller controls ordering so long runs can retain completed per-FPGA
  # evidence if a later placement is interrupted.
  for fpga in $fpgas; do
    phase7a_one "$fpga"
  done

  python3 - "$phase7a" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
total = 0
transport = 0
for fpga in ("fpga0", "fpga1", "fpga2", "fpga3"):
    lowering = json.loads((root / fpga / "lowering-report.json").read_text())
    phase2 = json.loads(
        (root / fpga / "placement-openparf/phase2_report.json").read_text()
    )
    placement = phase2["placement"]
    if lowering["status"] != "pass":
        raise SystemExit(f"{fpga}: lowering did not pass")
    if placement["status"] != "legal":
        raise SystemExit(f"{fpga}: placement is not legal")
    if placement["cells"] != lowering["instances"]:
        raise SystemExit(f"{fpga}: placement instance coverage mismatch")
    total += lowering["instances"]
    transport += lowering["transport_instances"]
    print(
        "EMUFLOW_NVDLA_PHASE7A_FPGA "
        f"status=pass fpga={fpga} cells={lowering['instances']} "
        f"transport_cells={lowering['transport_instances']} "
        f"sites_used={placement['sites_used']} provider={phase2['provider']}"
    )
print(
    "EMUFLOW_NVDLA_PHASE7A "
    f"status=pass merged_cells={total} transport_cells={transport}"
)
PY
}

phase7b_one() {
  local fpga="$1"
  local source="$phase7a/$fpga"
  local target="$phase7b/$fpga"
  local expected_cells
  local top
  local implementation_input

  require_file "$source/placement.emuir.json"
  require_file "$source/placement-openparf/placement.vivado.tsv"
  mkdir -p "$target"
  if [ "$resume" != 1 ] ||
    [ ! -s "$target/mapped.v" ] ||
    [ ! -s "$target/emission-report.json" ]; then
    /usr/bin/time -v -o "$target/emission-time.txt" \
      env PYTHONPATH="$repo/src" python3 -m emuflow emit-mapped-verilog \
        --ir "$source/placement.emuir.json" \
        --output "$target/mapped.v" \
        --report "$target/emission-report.json" \
        > "$target/emission-stdout.json"
  fi
  read -r expected_cells top < <(
    python3 - "$target/emission-report.json" <<'PY'
import json
import sys

report = json.load(open(sys.argv[1], encoding="utf-8"))
print(report["instances"], report["top"])
PY
  )
  implementation_input="$target/mapped.v"
  if list_contains "$sparse_anchor_fpgas" "$fpga"; then
    if [ "$fpga" = "fpga0" ] && [ -n "$main_unplaced_dcp" ]; then
      require_file "$main_unplaced_dcp"
      implementation_input="$main_unplaced_dcp"
    fi
    /usr/bin/time -v -o "$target/vivado-time.txt" \
      "$vivado" -mode batch -nojournal -nolog \
        -source "$repo/scripts/vivado/validate_mapped.tcl" \
        -tclargs "$part" \
          "$implementation_input" \
          "$top" \
          "$source/placement-openparf/placement.vivado.tsv" \
          "$target/vivado" \
          "$expected_cells" \
          nvdla_core_clk 256.0 \
          "$phase7c/runtime_timing.xdc" \
          1 "$main_place_directive" "$main_route_directive" \
          "$main_anchor_modulus" \
        > "$target/vivado-validation.log" 2>&1
  else
    /usr/bin/time -v -o "$target/vivado-time.txt" \
      "$vivado" -mode batch -nojournal -nolog \
        -source "$repo/scripts/vivado/validate_mapped.tcl" \
        -tclargs "$part" \
          "$target/mapped.v" \
          "$top" \
          "$source/placement-openparf/placement.vivado.tsv" \
          "$target/vivado" \
          "$expected_cells" \
          nvdla_core_clk 256.0 \
          "$phase7c/runtime_timing.xdc" \
        > "$target/vivado-validation.log" 2>&1
  fi
  grep 'EMUFLOW_MAPPED_VIVADO status=pass' \
    "$target/vivado-validation.log"
  require_file "$target/vivado/routed.dcp"
}

run_phase7b() {
  require_file "$phase7c/runtime_timing.xdc"
  require_file "$vivado"
  mkdir -p "$phase7b"

  for fpga in $fpgas; do
    phase7b_one "$fpga"
  done
}

run_phase7c_finalize() {
  local fpga
  local expected_cells
  local physical_dir="$phase7c/physical"
  local platform="$repo/platforms/virtual/xcvu9p_4fpga_mesh.json"

  require_file "$phase7c/runtime_contract.json"
  require_file "$phase7c/runtime_timing.xdc"
  require_file "$platform"
  require_file "$vivado"
  rm -rf "$physical_dir"
  mkdir -p "$physical_dir"

  for fpga in fpga0 fpga1 fpga2 fpga3; do
    require_file "$phase7a/$fpga/lowering-report.json"
    require_file "$phase7b/$fpga/emission-report.json"
    require_file "$phase7b/$fpga/vivado/routed.dcp"
    expected_cells="$(
      python3 -c \
        'import json,sys; print(json.load(open(sys.argv[1]))["instances"])' \
        "$phase7b/$fpga/emission-report.json"
    )"
    "$vivado" -mode batch -nojournal -nolog \
      -source "$repo/scripts/vivado/report_runtime_contract.tcl" \
      -tclargs "$phase7b/$fpga/vivado/routed.dcp" \
        "$physical_dir/$fpga" "$expected_cells" \
        "$phase7b/$fpga/vivado/cells_before_xdc.txt" \
      > "$physical_dir/$fpga-vivado-runtime.log" 2>&1
    grep 'EMUFLOW_RUNTIME_VIVADO status=pass' \
      "$physical_dir/$fpga-vivado-runtime.log"
  done

  python3 - "$root" "$part" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
part = sys.argv[2]
phase7c = root / "phase7c"
runtime = json.loads(
    (phase7c / "runtime_contract.json").read_text(encoding="utf-8")
)
records = []
for fpga in ("fpga0", "fpga1", "fpga2", "fpga3"):
    lowering = json.loads(
        (root / "phase7a" / fpga / "lowering-report.json").read_text(
            encoding="utf-8"
        )
    )
    emission = json.loads(
        (root / "phase7b" / fpga / "emission-report.json").read_text(
            encoding="utf-8"
        )
    )
    metrics = {}
    for line in (
        phase7c / "physical" / fpga / "runtime_metrics.tsv"
    ).read_text(encoding="utf-8").splitlines()[1:]:
        key, value = line.split("\t")
        metrics[key] = value
    checkpoint = root / "phase7b" / fpga / "vivado" / "routed.dcp"
    digest = hashlib.sha256()
    with checkpoint.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    transport_cells = lowering["transport_instances"]
    original_cells = lowering["instances"] - transport_cells
    if original_cells < 0:
        raise SystemExit(f"{fpga}: negative original-cell accounting")
    if emission["instances"] != lowering["instances"]:
        raise SystemExit(f"{fpga}: emitted/lowered cell count mismatch")
    if int(metrics["mapped_cells"]) != emission["instances"]:
        raise SystemExit(f"{fpga}: routed/emitted cell count mismatch")
    if int(metrics["physical_cells"]) != (
        int(metrics["mapped_cells"]) + int(metrics["infrastructure_cells"])
    ):
        raise SystemExit(f"{fpga}: physical cell accounting mismatch")
    records.append(
        {
            "fpga": fpga,
            "part": part,
            "original_cells": original_cells,
            "transport_cells": transport_cells,
            "routed_cells": int(metrics["mapped_cells"]),
            "physical_cells": int(metrics["physical_cells"]),
            "infrastructure_cells": int(metrics["infrastructure_cells"]),
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
                "path_presence": {
                    "dut": int(metrics["dut_path_present"]),
                    "fabric": int(metrics["fabric_path_present"]),
                    "fabric_to_dut": int(
                        metrics["fabric_to_dut_path_present"]
                    ),
                },
            },
            "clocks": {
                "fabric_period_ns": float(metrics["fabric_period_ns"]),
                "dut_period_ns": float(metrics["dut_period_ns"]),
            },
            "routed_dcp": {
                "bytes": checkpoint.stat().st_size,
                "sha256": digest.hexdigest(),
            },
        }
    )

summary = {
    "schema": "emuflow.phase7b-physical-summary/v1",
    "status": "pass",
    "design": runtime["design"],
    "platform": runtime["platform"],
    "fpgas": records,
}
(phase7c / "physical_summary.json").write_text(
    json.dumps(summary, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

  env PYTHONPATH="$repo/src" python3 -m emuflow phase7c \
    --schedule "$root/phase5/schedule.json" \
    --platform "$platform" \
    --phase3-report "$root/phase3/phase3_report.json" \
    --phase4-report "$root/phase4/phase4_report.json" \
    --phase5-report "$root/phase5/phase5_report.json" \
    --phase6-report "$root/phase6/phase6_report.json" \
    --physical-summary "$phase7c/physical_summary.json" \
    --simulation-frames 64 \
    --out "$phase7c" \
    > "$root/phase7c-final-stdout.json"

  /usr/bin/iverilog -g2012 -s virtual_runtime_controller_tb \
    -o "$phase7c/virtual_runtime_controller_simv" \
    "$phase7c/virtual_runtime_controller.sv" \
    "$phase7c/virtual_runtime_controller_tb.sv"
  /usr/bin/vvp "$phase7c/virtual_runtime_controller_simv" \
    > "$phase7c/virtual_runtime_controller_sim.log"
  grep 'EMUFLOW_RUNTIME_TB status=pass' \
    "$phase7c/virtual_runtime_controller_sim.log"

  python3 - "$phase7c" <<'PY'
import json
import sys
from pathlib import Path

phase7c = Path(sys.argv[1])
report = json.loads(
    (phase7c / "phase7c_report.json").read_text(encoding="utf-8")
)
qor = json.loads((phase7c / "qor_report.json").read_text(encoding="utf-8"))
if report["status"] != "pass" or qor["status"] != "pass":
    raise SystemExit("NVDLA Phase 7C did not reach physical pass")
physical = qor["physical"]
if physical["original_cells"] != 731313:
    raise SystemExit("NVDLA Phase 7C lost original design cells")
if physical["routed_cells"] != (
    physical["original_cells"] + physical["transport_cells"]
):
    raise SystemExit("NVDLA Phase 7C cell accounting mismatch")
if physical["physical_cells"] != (
    physical["routed_cells"] + physical["infrastructure_cells"]
):
    raise SystemExit("NVDLA Phase 7C physical cell accounting mismatch")
print(
    "EMUFLOW_NVDLA_PHASE7C "
    f"status=pass routed_cells={physical['routed_cells']} "
    f"physical_cells={physical['physical_cells']} "
    f"infrastructure_cells={physical['infrastructure_cells']} "
    f"original_cells={physical['original_cells']} "
    f"transport_cells={physical['transport_cells']} "
    f"unrouted_nets={physical['unrouted_nets']} "
    f"drc_violations={physical['drc_violations']} "
    f"worst_wns_ns={physical['worst_wns_ns']}"
)
PY
}

run_phase7d() {
  local platform="$repo/platforms/virtual/xcvu9p_4fpga_mesh.json"
  local platform_name="virtual_xcvu9p_4fpga_mesh"
  local source_commit_file="$repo/.emuflow-source-commit"
  local source_commit
  local output="$root/phase7d"
  local repeat="$root/phase7d-repeat"
  local fpga

  require_file "$source_commit_file"
  source_commit="$(tr -d '\n' < "$source_commit_file")"
  if [[ ! "$source_commit" =~ ^[0-9a-f]{40}$ ]]; then
    echo "invalid deployed source commit: $source_commit" >&2
    exit 1
  fi
  require_file "$platform"
  require_file "$root/phase1/phase1_report.json"
  require_file "$phase7c/phase7c_report.json"
  require_file "$phase7c/runtime_contract.json"
  require_file "$phase7c/qor_report.json"
  require_file "$phase7c/physical_summary.json"

  env PYTHONPATH="$repo/src" python3 \
    "$repo/scripts/benchmarks/nvdla_release_inventory.py" \
    "$repo" "$root" "$platform_name" "$root/benchmark_report.json"

  run_audit() {
    local target="$1"
    local stdout="$2"
    local -a args=(
      env "PYTHONPATH=$repo/src" python3 -m emuflow phase7d
      --benchmark-report "$root/benchmark_report.json"
      --phase3-report "$root/phase3/phase3_report.json"
      --phase4-report "$root/phase4/phase4_report.json"
      --phase5-report "$root/phase5/phase5_report.json"
      --phase6-report "$root/phase6/phase6_report.json"
      --phase7c-report "$phase7c/phase7c_report.json"
      --runtime-contract "$phase7c/runtime_contract.json"
      --qor-report "$phase7c/qor_report.json"
      --physical-summary "$phase7c/physical_summary.json"
      --platform "$platform"
    )
    for fpga in fpga0 fpga1 fpga2 fpga3; do
      args+=(
        --lowering-report
        "$fpga=$phase7a/$fpga/lowering-report.json"
        --placement-report
        "$fpga=$phase7a/$fpga/placement-openparf/phase2_report.json"
        --emission-report
        "$fpga=$phase7b/$fpga/emission-report.json"
      )
    done
    args+=(
      --artifact "synthesis.mapped_json=$root/synthesis/mapped.json"
      --artifact "global.emuir=$root/phase1/design.emuir.json"
      --artifact "partition.assignment=$root/phase3/assignment.json"
      --artifact "system.routes=$root/phase4/routes.json"
      --artifact "system.schedule=$root/phase5/schedule.json"
      --artifact "system.lane_map=$root/phase6/lane_map.json"
    )
    for fpga in fpga0 fpga1 fpga2 fpga3; do
      args+=(
        --artifact "$fpga.netlist=$phase6/$fpga/netlist.json"
        --artifact
        "$fpga.placement=$phase7a/$fpga/placement-openparf/placement.json"
        --artifact "$fpga.mapped_verilog=$phase7b/$fpga/mapped.v"
        --artifact "$fpga.routed_dcp=$phase7b/$fpga/vivado/routed.dcp"
      )
    done
    args+=(
      --artifact "runtime.contract=$phase7c/runtime_contract.json"
      --artifact "runtime.timing_xdc=$phase7c/runtime_timing.xdc"
      --artifact "runtime.physical_summary=$phase7c/physical_summary.json"
      --artifact "release.qor=$phase7c/qor_report.json"
      --source-commit "$source_commit"
      --out "$target"
    )
    /usr/bin/time -v -o "$target-time.txt" \
      "${args[@]}" > "$stdout"
  }

  rm -rf "$output" "$repeat"
  run_audit "$output" "$root/phase7d-stdout.json"
  run_audit "$repeat" "$root/phase7d-repeat-stdout.json"
  cmp "$output/release_manifest.json" "$repeat/release_manifest.json"

  python3 - "$output" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
manifest_path = root / "release_manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
report = json.loads((root / "phase7d_report.json").read_text(encoding="utf-8"))
if manifest["status"] != "pass" or report["status"] != "pass":
    raise SystemExit("NVDLA Phase 7D release audit did not pass")
if set(manifest["gates"]) != {f"G{index}" for index in range(10)}:
    raise SystemExit("NVDLA Phase 7D did not cover exactly G0-G9")
if any(gate["status"] != "pass" for gate in manifest["gates"].values()):
    raise SystemExit("NVDLA Phase 7D contains a failing gate")
metrics = manifest["metrics"]
expected = {
    "original_cells": 731313,
    "transport_cells": 74,
    "routed_cells": 731387,
    "physical_cells": 731388,
    "infrastructure_cells": 1,
    "cut_nets": 3,
    "scheduled_bit_hops": 4,
    "equivalence_cycles": 2,
}
for key, value in expected.items():
    if metrics[key] != value:
        raise SystemExit(f"NVDLA Phase 7D {key} mismatch")
if metrics["source_files"] < 250:
    raise SystemExit("NVDLA Phase 7D source inventory is incomplete")
if len(manifest["artifacts"]) != 26:
    raise SystemExit("NVDLA Phase 7D artifact inventory count mismatch")
digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
print(
    "EMUFLOW_NVDLA_PHASE7D "
    f"status=pass gates={len(manifest['gates'])} "
    f"source_files={metrics['source_files']} "
    f"artifacts={len(manifest['artifacts'])} "
    f"original_cells={metrics['original_cells']} "
    f"transport_cells={metrics['transport_cells']} "
    f"routed_cells={metrics['routed_cells']} "
    f"physical_cells={metrics['physical_cells']} "
    f"infrastructure_cells={metrics['infrastructure_cells']} "
    f"cut_nets={metrics['cut_nets']} "
    f"worst_wns_ns={metrics['worst_wns_ns']} "
    f"manifest_sha256={digest}"
)
PY
}

cd "$repo"
case "$command_name" in
  phase7a)
    run_phase7a
    ;;
  phase7b)
    run_phase7b
    ;;
  phase7c-finalize)
    run_phase7c_finalize
    ;;
  phase7d)
    run_phase7d
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
