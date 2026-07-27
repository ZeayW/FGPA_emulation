import math
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .errors import EmuFlowError, ValidationError
from .io import read_json, write_json
from .ir import EmuIR
from .partition import build_partition_assignment
from .platform import Platform
from .resources import RESOURCE_FIELDS


TRITONPART_INPUT_SCHEMA = "emuflow.tritonpart-input/v1"
PARTITION_NET_WEIGHTS_SCHEMA = "emuflow.partition-net-weights/v1"
TRITONPART_PROVIDER = "tritonpart-openroad-hypergraph-v1"


def load_partition_net_weights(path: Optional[Path]) -> Dict[str, float]:
    if path is None:
        return {}
    value = read_json(path)
    if value.get("schema") != PARTITION_NET_WEIGHTS_SCHEMA:
        raise ValidationError(
            "net weights schema: expected "
            f"{PARTITION_NET_WEIGHTS_SCHEMA!r}, got {value.get('schema')!r}"
        )
    raw_weights = value.get("weights")
    if not isinstance(raw_weights, dict):
        raise ValidationError("net weights.weights: expected an object")
    weights: Dict[str, float] = {}
    for net_id, raw_weight in raw_weights.items():
        if not isinstance(net_id, str) or not net_id:
            raise ValidationError(
                "net weights.weights: keys must be non-empty strings"
            )
        if (
            isinstance(raw_weight, bool)
            or not isinstance(raw_weight, (int, float))
            or not math.isfinite(float(raw_weight))
            or float(raw_weight) <= 0.0
        ):
            raise ValidationError(
                f"net weights.weights[{net_id!r}]: expected a positive number"
            )
        weights[net_id] = float(raw_weight)
    return weights


def _active_resource_fields(
    clusters: Sequence[Mapping[str, Any]],
    platform: Platform,
) -> List[str]:
    fields = []
    for field in RESOURCE_FIELDS:
        if not any(cluster["resources"].get(field, 0) for cluster in clusters):
            continue
        if not all(fpga.effective_capacity.get(field, 0) > 0 for fpga in platform.fpgas):
            continue
        fields.append(field)
    return fields


def _capacity_base_balance(
    platform: Platform,
    resource_fields: Sequence[str],
) -> List[float]:
    num_parts = len(platform.fpgas)
    if not resource_fields:
        return [1.0 / num_parts] * num_parts

    reference: Optional[List[float]] = None
    for field in resource_fields:
        capacities = [
            float(fpga.effective_capacity[field]) for fpga in platform.fpgas
        ]
        total = sum(capacities)
        shares = [capacity / total for capacity in capacities]
        if reference is None:
            reference = shares
            continue
        if any(
            not math.isclose(left, right, rel_tol=1e-6, abs_tol=1e-9)
            for left, right in zip(reference, shares)
        ):
            raise ValidationError(
                "TritonPart hypergraph mode cannot represent resource-specific "
                "heterogeneous FPGA capacity ratios; use homogeneous/proportionally "
                "scaled FPGAs or the greedy provider"
            )
    assert reference is not None
    return reference


def _vertex_weights(
    cluster: Mapping[str, Any],
    resource_fields: Sequence[str],
) -> List[int]:
    return [
        len(cluster["instances"]),
        *(cluster["resources"].get(field, 0) for field in resource_fields),
    ]


def _effective_balance_percent(
    vertex_weights: Sequence[Sequence[int]],
    clusters: Sequence[Mapping[str, Any]],
    fpga_ids: Sequence[str],
    base_balance: Sequence[float],
    requested_tolerance: float,
) -> Tuple[float, float]:
    totals = [
        sum(weights[index] for weights in vertex_weights)
        for index in range(len(vertex_weights[0]))
    ]
    required_fraction = 0.0
    largest_target = max(base_balance)
    for weights in vertex_weights:
        for index, total in enumerate(totals):
            if total:
                required_fraction = max(
                    required_fraction,
                    weights[index] / total - largest_target,
                )

    fixed_totals = [
        [0] * len(totals) for _ in fpga_ids
    ]
    fpga_index = {fpga_id: index for index, fpga_id in enumerate(fpga_ids)}
    for cluster, weights in zip(clusters, vertex_weights):
        fixed_fpga = cluster["fixed_fpga"]
        if fixed_fpga is None:
            continue
        target = fixed_totals[fpga_index[fixed_fpga]]
        for index, weight in enumerate(weights):
            target[index] += weight
    for part_index, weights in enumerate(fixed_totals):
        for dimension, total in enumerate(totals):
            if total:
                required_fraction = max(
                    required_fraction,
                    weights[dimension] / total - base_balance[part_index],
                )

    requested_percent = requested_tolerance * 100.0
    required_percent = max(0.0, required_fraction * 100.0)
    # TritonPart compares floating-point accumulated weights. A small,
    # deterministic guard prevents an exactly tight atomic cluster from being
    # rejected due to rounding.
    effective_percent = max(requested_percent, required_percent + 0.01)
    return requested_percent, effective_percent


def _legal_hyperedges(
    ir: EmuIR,
    cluster_by_instance: Mapping[str, str],
    vertex_number: Mapping[str, int],
    net_weights: Mapping[str, float],
) -> List[Dict[str, Any]]:
    known_nets = {net["id"] for net in ir.value["nets"]}
    unknown_weights = sorted(set(net_weights) - known_nets)
    if unknown_weights:
        raise ValidationError(
            f"net weights reference unknown nets {unknown_weights[:8]}"
        )

    hyperedges = []
    for net in ir.value["nets"]:
        if net["cut_class"] != "register_output":
            continue
        cluster_ids = sorted(
            {
                cluster_by_instance[endpoint["instance"]]
                for collection in ("drivers", "sinks")
                for endpoint in net[collection]
                if endpoint["instance"] is not None
            }
        )
        if len(cluster_ids) < 2:
            continue
        hyperedges.append(
            {
                "net": net["id"],
                "weight": float(net_weights.get(net["id"], 1.0)),
                "clusters": cluster_ids,
                "vertices": [vertex_number[cluster_id] for cluster_id in cluster_ids],
            }
        )
    return hyperedges


def export_tritonpart_inputs(
    ir: EmuIR,
    platform: Platform,
    clusters_artifact: Mapping[str, Any],
    constraints: Mapping[str, Any],
    output_dir: Path,
    net_weights: Optional[Mapping[str, float]] = None,
) -> Dict[str, Any]:
    clusters = sorted(clusters_artifact["clusters"], key=lambda item: item["id"])
    if len(clusters) < len(platform.fpgas):
        raise ValidationError(
            "TritonPart needs at least one atomic cluster per FPGA"
        )

    resource_fields = _active_resource_fields(clusters, platform)
    dimensions = ["cells", *resource_fields]
    weights = [_vertex_weights(cluster, resource_fields) for cluster in clusters]
    base_balance = _capacity_base_balance(platform, resource_fields)
    fpga_ids = [fpga.id for fpga in platform.fpgas]
    requested_balance, effective_balance = _effective_balance_percent(
        weights,
        clusters,
        fpga_ids,
        base_balance,
        constraints["balance_tolerance"],
    )

    for dimension_index, field in enumerate(resource_fields, start=1):
        total = sum(item[dimension_index] for item in weights)
        capacity = sum(
            fpga.effective_capacity[field] for fpga in platform.fpgas
        )
        if total > capacity:
            raise ValidationError(
                f"total {field} demand {total} exceeds effective platform "
                f"capacity {capacity}"
            )
    for cluster, item_weights in zip(clusters, weights):
        if not any(
            all(
                item_weights[index]
                <= fpga.effective_capacity[field]
                for index, field in enumerate(resource_fields, start=1)
            )
            for fpga in platform.fpgas
        ):
            raise ValidationError(
                f"atomic cluster {cluster['id']!r} cannot fit any FPGA"
            )

    vertex_number = {
        cluster["id"]: index for index, cluster in enumerate(clusters, start=1)
    }
    cluster_by_instance = {
        instance_id: cluster["id"]
        for cluster in clusters
        for instance_id in cluster["instances"]
    }
    hyperedges = _legal_hyperedges(
        ir,
        cluster_by_instance,
        vertex_number,
        net_weights or {},
    )
    if not hyperedges:
        raise ValidationError(
            "TritonPart hypergraph has no legal register-output hyperedges; "
            "use the greedy provider for disconnected designs"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    hypergraph_path = output_dir / "partition.hgr"
    fixed_path = output_dir / "partition.fix"
    tcl_path = output_dir / "run_tritonpart.tcl"
    solution_path = output_dir / f"partition.hgr.part.{len(fpga_ids)}"

    hgr_lines = [f"{len(hyperedges)} {len(clusters)} 11"]
    for edge in hyperedges:
        vertices = " ".join(str(vertex) for vertex in edge["vertices"])
        hgr_lines.append(f"{edge['weight']:.9g} {vertices}")
    hgr_lines.extend(" ".join(str(value) for value in item) for item in weights)
    hypergraph_path.write_text("\n".join(hgr_lines) + "\n", encoding="utf-8")

    fpga_index = {fpga_id: index for index, fpga_id in enumerate(fpga_ids)}
    fixed_path.write_text(
        "".join(
            (
                f"{fpga_index[cluster['fixed_fpga']]}\n"
                if cluster["fixed_fpga"] is not None
                else "-1\n"
            )
            for cluster in clusters
        ),
        encoding="utf-8",
    )

    def tcl_list(values: Sequence[Any]) -> str:
        return "{ " + " ".join(str(value) for value in values) + " }"

    tcl_lines = [
        "triton_part_hypergraph \\",
        f"  -hypergraph_file {{{hypergraph_path.resolve()}}} \\",
        f"  -fixed_file {{{fixed_path.resolve()}}} \\",
        f"  -num_parts {len(fpga_ids)} \\",
        f"  -balance_constraint {effective_balance:.9g} \\",
        f"  -base_balance {tcl_list([f'{value:.12g}' for value in base_balance])} \\",
        f"  -scale_factor {tcl_list([1.0] * len(fpga_ids))} \\",
        f"  -seed 0 \\",
        f"  -vertex_dimension {len(dimensions)} \\",
        "  -hyperedge_dimension 1 \\",
        f"  -v_wt_factors {tcl_list([1.0] * len(dimensions))} \\",
        "  -e_wt_factors { 1.0 } \\",
        "  -min_num_vertices_each_part 1",
        "exit",
    ]
    tcl_path.write_text("\n".join(tcl_lines) + "\n", encoding="utf-8")

    artifact: Dict[str, Any] = {
        "schema": TRITONPART_INPUT_SCHEMA,
        "design": ir.value["design"]["name"],
        "platform": platform.name,
        "fpga_order": fpga_ids,
        "cluster_order": [cluster["id"] for cluster in clusters],
        "vertex_dimensions": dimensions,
        "vertex_weights": weights,
        "hyperedges": hyperedges,
        "base_balance": base_balance,
        "requested_balance_percent": requested_balance,
        "effective_balance_percent": effective_balance,
        "balance_auto_relaxed": effective_balance > requested_balance + 1e-9,
        "files": {
            "hypergraph": hypergraph_path.name,
            "fixed": fixed_path.name,
            "tcl": tcl_path.name,
            "solution": solution_path.name,
        },
    }
    write_json(output_dir / "tritonpart_input.json", artifact)
    return artifact


def parse_tritonpart_solution(
    path: Path,
    tritonpart_input: Mapping[str, Any],
) -> Dict[str, str]:
    if not path.is_file():
        raise ValidationError(f"TritonPart solution does not exist: {path}")
    tokens = path.read_text(encoding="utf-8").split()
    cluster_order = tritonpart_input["cluster_order"]
    fpga_order = tritonpart_input["fpga_order"]
    if len(tokens) != len(cluster_order):
        raise ValidationError(
            "TritonPart solution vertex count mismatch: "
            f"expected {len(cluster_order)}, got {len(tokens)}"
        )
    assignment: Dict[str, str] = {}
    for index, (cluster_id, token) in enumerate(zip(cluster_order, tokens)):
        try:
            part_id = int(token)
        except ValueError as error:
            raise ValidationError(
                f"TritonPart solution line {index + 1}: expected an integer"
            ) from error
        if part_id < 0 or part_id >= len(fpga_order):
            raise ValidationError(
                f"TritonPart solution line {index + 1}: invalid part {part_id}"
            )
        assignment[cluster_id] = fpga_order[part_id]
    return assignment


def run_tritonpart(
    ir: EmuIR,
    platform: Platform,
    clusters_artifact: Mapping[str, Any],
    constraints: Mapping[str, Any],
    output_dir: Path,
    seed: int,
    executable: Optional[str] = None,
    solution_input: Optional[Path] = None,
    net_weights: Optional[Mapping[str, float]] = None,
    timeout_seconds: int = 3600,
) -> Dict[str, Any]:
    tritonpart_input = export_tritonpart_inputs(
        ir,
        platform,
        clusters_artifact,
        constraints,
        output_dir,
        net_weights=net_weights,
    )
    tcl_path = output_dir / tritonpart_input["files"]["tcl"]
    # The seed is deliberately patched after exporting so the input metadata
    # and generated command remain an exact record of the executed run.
    tcl_text = tcl_path.read_text(encoding="utf-8").replace(
        "  -seed 0 \\\n", f"  -seed {seed} \\\n"
    )
    tcl_path.write_text(tcl_text, encoding="utf-8")
    tritonpart_input["seed"] = seed
    write_json(output_dir / "tritonpart_input.json", tritonpart_input)

    solution_path = output_dir / tritonpart_input["files"]["solution"]
    log_path = output_dir / "openroad-tritonpart.log"
    resolved_executable: Optional[str] = None
    mode = "execute"
    if solution_input is not None:
        if not solution_input.is_file():
            raise ValidationError(
                f"precomputed TritonPart solution does not exist: {solution_input}"
            )
        if solution_input.resolve() != solution_path.resolve():
            shutil.copyfile(solution_input, solution_path)
        mode = "import"
    else:
        resolved_executable = executable or shutil.which("openroad")
        if not resolved_executable:
            raise EmuFlowError(
                "OpenROAD executable was not found; install OpenROAD with "
                "TritonPart or pass --openroad PATH"
            )
        if solution_path.exists():
            solution_path.unlink()
        try:
            completed = subprocess.run(
                [resolved_executable, "-exit", str(tcl_path.resolve())],
                cwd=output_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise EmuFlowError(
                f"TritonPart exceeded timeout of {timeout_seconds} seconds"
            ) from error
        log_path.write_text(completed.stdout, encoding="utf-8")
        if completed.returncode != 0:
            tail = "\n".join(completed.stdout.splitlines()[-30:])
            raise EmuFlowError(
                "OpenROAD/TritonPart failed with exit code "
                f"{completed.returncode}\n{tail}"
            )
        if not solution_path.is_file():
            raise EmuFlowError(
                "OpenROAD/TritonPart reported success but did not create "
                f"{solution_path}"
            )

    cluster_assignment = parse_tritonpart_solution(
        solution_path, tritonpart_input
    )
    return build_partition_assignment(
        ir,
        platform,
        clusters_artifact,
        constraints,
        cluster_assignment,
        provider=TRITONPART_PROVIDER,
        seed=seed,
        provider_metadata={
            "mode": mode,
            "executable": resolved_executable,
            "input_schema": TRITONPART_INPUT_SCHEMA,
            "vertex_dimensions": tritonpart_input["vertex_dimensions"],
            "hyperedges": len(tritonpart_input["hyperedges"]),
            "requested_balance_percent": tritonpart_input[
                "requested_balance_percent"
            ],
            "effective_balance_percent": tritonpart_input[
                "effective_balance_percent"
            ],
            "balance_auto_relaxed": tritonpart_input["balance_auto_relaxed"],
            "artifacts": {
                **tritonpart_input["files"],
                "input": "tritonpart_input.json",
                "log": log_path.name if mode == "execute" else None,
            },
        },
    )
