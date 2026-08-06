"""Source-visible distributed runtime startup synchronization contract."""

from __future__ import annotations

import hashlib
from collections import deque
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .errors import ValidationError
from .io import read_json, write_json
from .platform import Platform


RUNTIME_SYNC_PROVIDER_SCHEMA = "emuflow.runtime-sync-provider/v1"
RUNTIME_SYNC_TOPOLOGY_SCHEMA = "emuflow.runtime-sync-topology/v1"
RUNTIME_SYNC_NODE_MODULE = "emuflow_runtime_sync_tree_node"
VALID_QUALIFICATIONS = {"editable_source_hardware", "simulation_only"}


def _text(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{context}: expected a non-empty string")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_runtime_sync_provider(
    manifest: Mapping[str, Any], manifest_path: Path
) -> Dict[str, Any]:
    if manifest.get("schema") != RUNTIME_SYNC_PROVIDER_SCHEMA:
        raise ValidationError(
            f"runtime sync provider schema must be {RUNTIME_SYNC_PROVIDER_SCHEMA!r}"
        )
    provider_id = _text(manifest.get("id"), "runtime_sync_provider.id")
    qualification = manifest.get("qualification")
    if qualification not in VALID_QUALIFICATIONS:
        raise ValidationError("runtime sync provider qualification is invalid")
    if manifest.get("modules") != {"tree_node": RUNTIME_SYNC_NODE_MODULE}:
        raise ValidationError("runtime sync provider module inventory is invalid")
    if manifest.get("implementation") != {
        "algorithm": "rooted-tree-future-epoch-barrier",
        "fault_policy": "sticky-fault-requires-global-reset",
    }:
        raise ValidationError("runtime sync implementation contract is invalid")
    if manifest.get("requirements") != {
        "fabric_clock": "phase-aligned-common-frequency",
        "reset_release": "synchronous-all-fpgas",
        "control_transport": "lossless-ordered-tree-links",
    }:
        raise ValidationError("runtime sync board requirements are invalid")

    source_root_text = _text(
        manifest.get("source_root"), "runtime_sync_provider.source_root"
    )
    source_root = (manifest_path.parent / source_root_text).resolve()
    raw_sources = manifest.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ValidationError("runtime sync provider sources are missing")
    normalized_sources = []
    module_found = False
    seen = set()
    for index, raw in enumerate(raw_sources):
        if not isinstance(raw, dict):
            raise ValidationError(f"runtime sync sources[{index}] is invalid")
        relative = Path(_text(raw.get("path"), f"runtime sync sources[{index}].path"))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValidationError("runtime sync source path must stay under source_root")
        if relative.as_posix() in seen:
            raise ValidationError("runtime sync provider repeats a source path")
        seen.add(relative.as_posix())
        language = raw.get("language")
        if language not in {"systemverilog", "verilog"}:
            raise ValidationError("runtime sync source language is invalid")
        role = _text(raw.get("role"), f"runtime sync sources[{index}].role")
        expected_digest = raw.get("sha256")
        if (
            not isinstance(expected_digest, str)
            or len(expected_digest) != 64
            or any(character not in "0123456789abcdef" for character in expected_digest)
        ):
            raise ValidationError("runtime sync source digest is invalid")
        source = (source_root / relative).resolve()
        try:
            source.relative_to(source_root)
        except ValueError as exc:
            raise ValidationError("runtime sync source escapes source_root") from exc
        if not source.is_file():
            raise ValidationError(f"runtime sync source does not exist: {relative}")
        actual_digest = _sha256(source)
        if actual_digest != expected_digest:
            raise ValidationError(f"runtime sync source digest mismatch: {relative}")
        source_text = source.read_text(encoding="utf-8")
        if f"module {RUNTIME_SYNC_NODE_MODULE}" in source_text:
            module_found = True
        normalized_sources.append(
            {
                "path": relative.as_posix(),
                "language": language,
                "role": role,
                "sha256": actual_digest,
                "absolute_path": str(source),
            }
        )
    if not module_found:
        raise ValidationError("runtime sync tree-node module is absent from sources")
    provenance = manifest.get("provenance")
    if not isinstance(provenance, dict):
        raise ValidationError("runtime sync provider provenance is missing")
    license_name = _text(provenance.get("license"), "provenance.license")
    upstream = _text(provenance.get("upstream"), "provenance.upstream")
    normalized = {
        "schema": RUNTIME_SYNC_PROVIDER_SCHEMA,
        "id": provider_id,
        "qualification": qualification,
        "modules": {"tree_node": RUNTIME_SYNC_NODE_MODULE},
        "implementation": dict(manifest["implementation"]),
        "source_root": str(source_root),
        "sources": normalized_sources,
        "requirements": dict(manifest["requirements"]),
        "provenance": {"license": license_name, "upstream": upstream},
    }
    return {
        "status": "pass",
        "provider": provider_id,
        "qualification": qualification,
        "sources": len(normalized_sources),
        "normalized": normalized,
    }


def build_runtime_sync_topology(
    platform: Platform,
    provider: Mapping[str, Any],
    root: Optional[str] = None,
    ready_stable_cycles: int = 4,
) -> Dict[str, Any]:
    fpga_ids = sorted(fpga.id for fpga in platform.fpgas)
    if not fpga_ids:
        raise ValidationError("runtime sync requires at least one FPGA")
    root = root or fpga_ids[0]
    if root not in fpga_ids:
        raise ValidationError(f"runtime sync root is not in BoardDB: {root}")
    if isinstance(ready_stable_cycles, bool) or ready_stable_cycles < 1:
        raise ValidationError("runtime sync ready_stable_cycles must be positive")

    adjacency: Dict[str, list[tuple[str, str, int]]] = {
        fpga_id: [] for fpga_id in fpga_ids
    }
    for link in platform.links:
        left, right = link.endpoints
        control_latency = max(1, link.latency_cycles)
        adjacency[left].append((right, link.id, control_latency))
        adjacency[right].append((left, link.id, control_latency))
    for neighbors in adjacency.values():
        neighbors.sort()

    parent: Dict[str, Optional[str]] = {root: None}
    parent_link: Dict[str, Optional[str]] = {root: None}
    edge_latency: Dict[str, int] = {root: 0}
    depth: Dict[str, int] = {root: 0}
    latency_from_root: Dict[str, int] = {root: 0}
    queue = deque([root])
    while queue:
        current = queue.popleft()
        for neighbor, link_id, latency in adjacency[current]:
            if neighbor in parent:
                continue
            parent[neighbor] = current
            parent_link[neighbor] = link_id
            edge_latency[neighbor] = latency
            depth[neighbor] = depth[current] + 1
            # The serialized control word spends ``latency`` cycles on the
            # link and one more receiver clock edge entering the child node.
            # Omitting the receiver boundary lets a deepest child observe the
            # target epoch too late to arm for that same epoch.
            latency_from_root[neighbor] = (
                latency_from_root[current] + latency + 1
            )
            queue.append(neighbor)
    if set(parent) != set(fpga_ids):
        missing = sorted(set(fpga_ids) - set(parent))
        raise ValidationError(
            f"runtime sync requires a connected BoardDB graph; unreachable: {missing}"
        )
    children = {fpga_id: [] for fpga_id in fpga_ids}
    for fpga_id in fpga_ids:
        if parent[fpga_id] is not None:
            children[parent[fpga_id]].append(fpga_id)
    for child_list in children.values():
        child_list.sort()

    max_broadcast_latency = max(latency_from_root.values(), default=0)
    start_margin_cycles = max_broadcast_latency + 2
    nodes = []
    for fpga_id in fpga_ids:
        nodes.append(
            {
                "fpga": fpga_id,
                "role": "root" if fpga_id == root else "branch_or_leaf",
                "parent": parent[fpga_id],
                "parent_link": parent_link[fpga_id],
                "parent_control_latency_cycles": edge_latency[fpga_id],
                "children": children[fpga_id],
                "depth": depth[fpga_id],
                "broadcast_latency_from_root_cycles": latency_from_root[fpga_id],
            }
        )
    return {
        "schema": RUNTIME_SYNC_TOPOLOGY_SCHEMA,
        "platform": platform.name,
        "provider": provider["id"],
        "algorithm": "rooted-tree-future-epoch-barrier",
        "root": root,
        "parameters": {
            "epoch_bits": 32,
            "ready_stable_cycles": ready_stable_cycles,
            "start_margin_cycles": start_margin_cycles,
            "max_broadcast_latency_cycles": max_broadcast_latency,
        },
        "nodes": nodes,
        "fault_policy": "sticky-fault-requires-global-reset",
        "board_proof": {
            "fabric_clock_phase_alignment": "required_unproven",
            "synchronous_reset_release": "required_unproven",
            "control_transport": "required_unbound",
        },
        "hardware_release_status": "blocked_on_board_sync_binding",
    }


def validate_runtime_sync_topology(
    topology: Mapping[str, Any], platform: Platform, provider: Mapping[str, Any]
) -> Dict[str, Any]:
    expected = build_runtime_sync_topology(
        platform,
        provider,
        root=topology.get("root"),
        ready_stable_cycles=topology.get("parameters", {}).get(
            "ready_stable_cycles", 0
        ),
    )
    if topology != expected:
        raise ValidationError("runtime sync topology is not reproducible")
    return {
        "status": "pass",
        "fpgas": len(expected["nodes"]),
        "root": expected["root"],
        "start_margin_cycles": expected["parameters"]["start_margin_cycles"],
        "hardware_release_status": expected["hardware_release_status"],
    }


def runtime_sync_testbench(topology: Mapping[str, Any]) -> str:
    """Generate a latency-aware HDL testbench for one synchronization tree."""
    if topology.get("schema") != RUNTIME_SYNC_TOPOLOGY_SCHEMA:
        raise ValidationError("runtime sync testbench requires a v1 topology")
    nodes = topology["nodes"]
    by_id = {node["fpga"]: node for node in nodes}
    index = {fpga_id: number for number, fpga_id in enumerate(sorted(by_id))}
    parameters = topology["parameters"]
    epoch_bits = parameters["epoch_bits"]
    lines = [
        "`timescale 1ns/1ps",
        "module emuflow_runtime_sync_tree_tb;",
        f"  localparam integer EPOCH_BITS = {epoch_bits};",
        "  reg fabric_clk = 1'b0;",
        "  reg reset = 1'b1;",
    ]
    for fpga_id in sorted(by_id):
        stem = f"n{index[fpga_id]}"
        child_count = len(by_id[fpga_id]["children"])
        child_ports = max(1, child_count)
        lines.extend(
            [
                f"  reg {stem}_local_ready = 1'b0;",
                f"  wire [{child_ports - 1}:0] {stem}_child_ready;",
                f"  wire {stem}_parent_start_valid;",
                f"  wire [EPOCH_BITS-1:0] {stem}_parent_start_epoch;",
                f"  wire {stem}_subtree_ready;",
                f"  wire {stem}_child_start_valid;",
                f"  wire [EPOCH_BITS-1:0] {stem}_child_start_epoch;",
                f"  wire {stem}_global_ready;",
                f"  wire {stem}_faulted;",
                f"  wire [EPOCH_BITS-1:0] {stem}_epoch;",
            ]
        )
    lines.append("")
    edge_records = []
    for child_id in sorted(by_id):
        child = by_id[child_id]
        parent_id = child["parent"]
        if parent_id is None:
            continue
        latency = child["parent_control_latency_cycles"]
        child_stem = f"n{index[child_id]}"
        parent_stem = f"n{index[parent_id]}"
        edge = f"e{index[parent_id]}_{index[child_id]}"
        edge_records.append((parent_id, child_id, edge))
        lines.extend(
            [
                f"  reg [{latency - 1}:0] {edge}_ready_pipe;",
                f"  reg [{latency - 1}:0] {edge}_start_valid_pipe;",
                f"  reg [EPOCH_BITS-1:0] {edge}_start_epoch_pipe [0:{latency - 1}];",
                "  integer i_" + edge + ";",
                "  always @(posedge fabric_clk) begin",
                "    if (reset) begin",
                f"      {edge}_ready_pipe <= {latency}'b0;",
                f"      {edge}_start_valid_pipe <= {latency}'b0;",
                f"      for (i_{edge} = 0; i_{edge} < {latency}; i_{edge} = i_{edge} + 1)",
                f"        {edge}_start_epoch_pipe[i_{edge}] <= {{EPOCH_BITS{{1'b0}}}};",
                "    end else begin",
                f"      {edge}_ready_pipe[0] <= {child_stem}_subtree_ready;",
                f"      {edge}_start_valid_pipe[0] <= {parent_stem}_child_start_valid;",
                f"      {edge}_start_epoch_pipe[0] <= {parent_stem}_child_start_epoch;",
                f"      for (i_{edge} = 1; i_{edge} < {latency}; i_{edge} = i_{edge} + 1) begin",
                f"        {edge}_ready_pipe[i_{edge}] <= {edge}_ready_pipe[i_{edge}-1];",
                f"        {edge}_start_valid_pipe[i_{edge}] <= {edge}_start_valid_pipe[i_{edge}-1];",
                f"        {edge}_start_epoch_pipe[i_{edge}] <= {edge}_start_epoch_pipe[i_{edge}-1];",
                "      end",
                "    end",
                "  end",
                f"  assign {child_stem}_parent_start_valid = {edge}_start_valid_pipe[{latency - 1}];",
                f"  assign {child_stem}_parent_start_epoch = {edge}_start_epoch_pipe[{latency - 1}];",
                "",
            ]
        )
    root_stem = f"n{index[topology['root']]}"
    lines.extend(
        [
            f"  assign {root_stem}_parent_start_valid = 1'b0;",
            f"  assign {root_stem}_parent_start_epoch = {{EPOCH_BITS{{1'b0}}}};",
        ]
    )
    for parent_id in sorted(by_id):
        parent = by_id[parent_id]
        parent_stem = f"n{index[parent_id]}"
        child_ports = max(1, len(parent["children"]))
        pieces = []
        for child_id in reversed(parent["children"]):
            edge = next(
                record[2]
                for record in edge_records
                if record[0] == parent_id and record[1] == child_id
            )
            latency = by_id[child_id]["parent_control_latency_cycles"]
            pieces.append(f"{edge}_ready_pipe[{latency - 1}]")
        if not pieces:
            pieces = ["1'b0"]
        lines.append(
            f"  assign {parent_stem}_child_ready = {{{', '.join(pieces)}}};"
        )
        active_mask = (1 << len(parent["children"])) - 1
        lines.extend(
            [
                f"  emuflow_runtime_sync_tree_node #(",
                f"    .EPOCH_BITS(EPOCH_BITS),",
                f"    .CHILD_PORTS({child_ports}),",
                f"    .ACTIVE_CHILD_MASK({child_ports}'h{active_mask:x}),",
                f"    .IS_ROOT({1 if parent_id == topology['root'] else 0}),",
                f"    .START_MARGIN_CYCLES({parameters['start_margin_cycles']}),",
                f"    .READY_STABLE_CYCLES({parameters['ready_stable_cycles']})",
                f"  ) dut_{parent_stem} (",
                f"    .fabric_clk(fabric_clk), .reset(reset),",
                f"    .local_ready({parent_stem}_local_ready),",
                f"    .child_subtree_ready({parent_stem}_child_ready),",
                f"    .parent_start_valid({parent_stem}_parent_start_valid),",
                f"    .parent_start_epoch({parent_stem}_parent_start_epoch),",
                f"    .subtree_ready({parent_stem}_subtree_ready),",
                f"    .child_start_valid({parent_stem}_child_start_valid),",
                f"    .child_start_epoch({parent_stem}_child_start_epoch),",
                f"    .global_ready({parent_stem}_global_ready),",
                f"    .faulted({parent_stem}_faulted), .epoch({parent_stem}_epoch)",
                "  );",
                "",
            ]
        )
    all_ready = " & ".join(
        f"n{index[fpga_id]}_global_ready" for fpga_id in sorted(by_id)
    )
    any_ready = " | ".join(
        f"n{index[fpga_id]}_global_ready" for fpga_id in sorted(by_id)
    )
    epoch_equal = " && ".join(
        f"n{index[fpga_id]}_epoch == {root_stem}_epoch"
        for fpga_id in sorted(by_id)
        if fpga_id != topology["root"]
    ) or "1'b1"
    leaf_id = next(
        fpga_id for fpga_id in sorted(by_id, reverse=True)
        if not by_id[fpga_id]["children"]
    )
    leaf_stem = f"n{index[leaf_id]}"
    lines.extend(
        [
            "  always #2 fabric_clk = ~fabric_clk;",
            "  integer cycles = 0;",
            "  initial begin",
            "    repeat (4) @(posedge fabric_clk);",
            "    @(negedge fabric_clk);",
            "    reset = 1'b0;",
            *[
                f"    n{index[fpga_id]}_local_ready = 1'b1;"
                for fpga_id in sorted(by_id)
            ],
            f"    while (!({any_ready}) && cycles < 10000) begin",
            "      @(posedge fabric_clk); #1; cycles = cycles + 1;",
            "    end",
            f"    if (!({all_ready}))",
            '      $fatal(1, "nodes did not release on the same epoch");',
            f"    if (!({epoch_equal}))",
            '      $fatal(1, "phase-aligned epoch counters diverged");',
            f"    {leaf_stem}_local_ready = 1'b0;",
            f"    repeat ({2 * parameters['max_broadcast_latency_cycles'] + 2}) @(posedge fabric_clk); #1;",
            f"    if (!{leaf_stem}_faulted || {leaf_stem}_global_ready)",
            '      $fatal(1, "post-release local fault was not sticky");',
            f"    if (!{root_stem}_faulted || {root_stem}_global_ready)",
            '      $fatal(1, "descendant fault did not propagate to the root");',
            '    $display("EMUFLOW_RUNTIME_SYNC_TB status=pass cycles=%0d epoch=%0d", cycles, '
            f"{root_stem}_epoch);",
            "    $finish;",
            "  end",
            "endmodule",
            "",
        ]
    )
    return "\n".join(lines)


def run_runtime_sync_materialization(
    platform_path: Path,
    provider_path: Path,
    output_dir: Path,
    root: Optional[str] = None,
    ready_stable_cycles: int = 4,
) -> Dict[str, Any]:
    platform = Platform.load(platform_path)
    provider_result = validate_runtime_sync_provider(
        read_json(provider_path), provider_path
    )
    provider = provider_result["normalized"]
    topology = build_runtime_sync_topology(
        platform, provider, root=root, ready_stable_cycles=ready_stable_cycles
    )
    validate_runtime_sync_topology(topology, platform, provider)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "runtime_sync_provider.normalized.json", provider)
    write_json(output_dir / "runtime_sync_topology.json", topology)
    testbench = runtime_sync_testbench(topology)
    (output_dir / "runtime_sync_tree_tb.sv").write_text(
        testbench, encoding="utf-8"
    )
    materialized_sources = []
    for source_record in provider["sources"]:
        if source_record["language"] not in {"systemverilog", "verilog"}:
            continue
        source = Path(source_record["absolute_path"])
        relative = Path("provider_sources") / source_record["path"]
        destination = output_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        materialized_sources.append(relative.as_posix())
    return {
        "schema": "emuflow.runtime-sync-materialization-report/v1",
        "status": "pass",
        "platform": platform.name,
        "provider": provider["id"],
        "hardware_release_status": topology["hardware_release_status"],
        "validation": {
            "fpgas": len(topology["nodes"]),
            "root": topology["root"],
            "start_margin_cycles": topology["parameters"][
                "start_margin_cycles"
            ],
        },
        "artifacts": {
            "provider": "runtime_sync_provider.normalized.json",
            "topology": "runtime_sync_topology.json",
            "rtl": materialized_sources,
            "testbench": "runtime_sync_tree_tb.sv",
        },
    }
