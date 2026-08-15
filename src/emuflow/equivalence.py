import hashlib
import json
from collections import defaultdict
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

from .errors import ValidationError
from .ir import EmuIR


def _bit(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value & 1
    text = str(value).strip().lower()
    if text in {"1", "1'b1", "true"}:
        return 1
    if text in {"0", "1'b0", "false", "x", "z"}:
        return 0
    return int(text, 2) & 1


def _stimulus(seed: int, cycle: int, port: str, bit: int) -> int:
    lower = port.lower()
    if lower in {"clk", "clock"}:
        return 0
    if lower.endswith(("resetn", "reset_n", "rstn", "rst_n")):
        return int(cycle >= 3)
    if lower in {"reset", "rst", "areset"}:
        return int(cycle < 3)
    digest = hashlib.sha256(
        f"{seed}:{cycle}:{port}:{bit}".encode("utf-8")
    ).digest()
    return digest[0] & 1


def _reset_deasserted_value(port: str) -> Optional[int]:
    lower = port.lower()
    if lower.endswith(("resetn", "reset_n", "rstn", "rst_n")):
        return 1
    if lower in {"reset", "rst", "areset"}:
        return 0
    return None


def _is_lut_type(cell_type: str) -> bool:
    return cell_type.startswith("LUT") or cell_type in {"$lut", "$_LUT_"}


def _is_ff_type(cell_type: str) -> bool:
    return cell_type in {"FDCE", "FDPE", "FDRE", "FDSE"} or (
        cell_type.startswith("$_DFF_")
    )


def _is_multiply_type(cell_type: str) -> bool:
    return cell_type == "VTR_MULTIPLY"


def _is_ram_type(cell_type: str) -> bool:
    return cell_type in {"VTR_SP_RAM", "VTR_DP_RAM"}


def _parameter_int(instance: Mapping[str, Any], name: str) -> int:
    raw = instance.get("parameters", {}).get(name)
    if isinstance(raw, bool):
        return int(raw)
    if isinstance(raw, int):
        return raw
    text = str(raw).strip()
    if not text:
        raise ValidationError(
            f"instance {instance['id']!r} lacks integer parameter {name}"
        )
    if text.lower().startswith(("0x", "0b", "0o")):
        return int(text, 0)
    if set(text) <= {"0", "1"}:
        return int(text, 2)
    return int(text, 10)


def _lut_definition(
    instance: Mapping[str, Any],
) -> Tuple[int, str, str, int]:
    cell_type = instance["type"]
    parameters = instance.get("parameters", {})
    if cell_type == "$lut":
        width_text = str(parameters.get("WIDTH", ""))
        truth_text = str(parameters.get("LUT", ""))
        if not width_text or not truth_text:
            raise ValidationError(
                f"generic LUT {instance['id']!r} lacks WIDTH/LUT parameters"
            )
        return int(width_text, 2), "A", "Y", int(truth_text, 2)
    if cell_type == "$_LUT_":
        width_text = str(parameters.get("WIDTH", ""))
        truth_text = str(parameters.get("LUT", parameters.get("INIT", "")))
        if not width_text or not truth_text:
            raise ValidationError(
                f"generic LUT {instance['id']!r} lacks WIDTH/LUT parameters"
            )
        return int(width_text, 2), "A", "Y", int(truth_text, 2)
    width_text = cell_type[3:]
    if not width_text.isdigit():
        raise ValidationError(f"unsupported LUT primitive {cell_type!r}")
    init = parameters.get("INIT")
    if init is None:
        raise ValidationError(f"LUT {instance['id']!r} lacks INIT parameter")
    return int(width_text), "I", "O", int(str(init), 2)


class _MappedModel:
    def __init__(self, ir: EmuIR):
        self.ir = ir
        self.instances = {
            instance["id"]: instance for instance in ir.value["instances"]
        }
        unsupported = sorted(
            {
                instance["type"]
                for instance in self.instances.values()
                if not (
                    _is_lut_type(instance["type"])
                    or _is_ff_type(instance["type"])
                    or _is_multiply_type(instance["type"])
                    or _is_ram_type(instance["type"])
                )
            }
        )
        if unsupported:
            raise ValidationError(
                "cycle equivalence primitive model does not support "
                f"{unsupported}"
            )
        self.input_net: Dict[Tuple[str, str, int], str] = {}
        self.output_net: Dict[Tuple[str, str, int], str] = {}
        self.top_input_net: Dict[Tuple[str, int], str] = {}
        self.top_output_net: Dict[Tuple[str, int], str] = {}
        for net in ir.value["nets"]:
            for endpoint in net["drivers"]:
                key = (endpoint["port"], endpoint["bit"])
                if endpoint["instance"] is None:
                    self.top_input_net[key] = net["id"]
                else:
                    self.output_net[
                        (
                            endpoint["instance"],
                            endpoint["port"],
                            endpoint["bit"],
                        )
                    ] = net["id"]
            for endpoint in net["sinks"]:
                key = (endpoint["port"], endpoint["bit"])
                if endpoint["instance"] is None:
                    self.top_output_net[key] = net["id"]
                else:
                    self.input_net[
                        (
                            endpoint["instance"],
                            endpoint["port"],
                            endpoint["bit"],
                        )
                    ] = net["id"]
        self.constants: Dict[Tuple[str, str, int], int] = {}
        for instance in self.instances.values():
            for item in instance.get("constant_connections", []):
                self.constants[
                    (instance["id"], item["port"], item["bit"])
                ] = _bit(item["value"])
        self.ff_ids = sorted(
            instance_id
            for instance_id, instance in self.instances.items()
            if _is_ff_type(instance["type"])
        )
        self.lut_ids = sorted(
            instance_id
            for instance_id, instance in self.instances.items()
            if _is_lut_type(instance["type"])
        )
        self.multiply_ids = sorted(
            instance_id
            for instance_id, instance in self.instances.items()
            if _is_multiply_type(instance["type"])
        )
        self.ram_ids = sorted(
            instance_id
            for instance_id, instance in self.instances.items()
            if _is_ram_type(instance["type"])
        )

    def initial_state(self) -> Dict[str, Any]:
        state: Dict[str, Any] = {
            instance_id: _bit(
                self.instances[instance_id]
                .get("parameters", {})
                .get("INIT", 0)
            )
            for instance_id in self.ff_ids
        }
        for instance_id in self.ram_ids:
            instance = self.instances[instance_id]
            width = _parameter_int(instance, "DATA_WIDTH")
            state[instance_id] = {
                "contents": {},
                **(
                    {"out": [0] * width}
                    if instance["type"] == "VTR_SP_RAM"
                    else {"out1": [0] * width, "out2": [0] * width}
                ),
            }
        return state

    def _pin(
        self,
        values: Mapping[str, int],
        instance_id: str,
        port: str,
        default: int = 0,
        overrides: Optional[Mapping[Tuple[str, str], int]] = None,
        bit: int = 0,
    ) -> Optional[int]:
        net = self.input_net.get((instance_id, port, bit))
        if net is not None:
            if overrides is not None and (instance_id, net) in overrides:
                return overrides[(instance_id, net)]
            return values.get(net)
        return self.constants.get((instance_id, port, bit), default)

    def _bus(
        self,
        values: Mapping[str, int],
        instance_id: str,
        port: str,
        width: int,
        overrides: Optional[Mapping[Tuple[str, str], int]] = None,
    ) -> Optional[int]:
        bits = [
            self._pin(
                values,
                instance_id,
                port,
                overrides=overrides,
                bit=bit,
            )
            for bit in range(width)
        ]
        if any(value is None for value in bits):
            return None
        return sum(int(value) << bit for bit, value in enumerate(bits))

    def _drive_bus(
        self,
        values: Dict[str, int],
        instance_id: str,
        port: str,
        value: int,
        width: int,
    ) -> None:
        for bit in range(width):
            net = self.output_net.get((instance_id, port, bit))
            if net is not None:
                values[net] = (value >> bit) & 1

    def evaluate(
        self,
        state: Mapping[str, Any],
        cycle: int,
        seed: int,
        overrides: Optional[Mapping[Tuple[str, str], int]] = None,
        input_values: Optional[Mapping[Tuple[str, int], int]] = None,
    ) -> Tuple[Dict[str, int], Dict[str, Any], Dict[str, int]]:
        values: Dict[str, int] = {}
        for (port, bit), net in self.top_input_net.items():
            if input_values is not None and (port, bit) in input_values:
                values[net] = _bit(input_values[(port, bit)])
            else:
                values[net] = _stimulus(seed, cycle, port, bit)
        for instance_id in self.ff_ids:
            q_net = self.output_net.get((instance_id, "Q", 0))
            if q_net is not None:
                values[q_net] = state[instance_id]
        for instance_id in self.ram_ids:
            instance = self.instances[instance_id]
            width = _parameter_int(instance, "DATA_WIDTH")
            ram_state = state[instance_id]
            if instance["type"] == "VTR_SP_RAM":
                self._drive_bus(
                    values,
                    instance_id,
                    "out",
                    sum(
                        int(bit) << index
                        for index, bit in enumerate(ram_state["out"])
                    ),
                    width,
                )
            else:
                for port in ("out1", "out2"):
                    self._drive_bus(
                        values,
                        instance_id,
                        port,
                        sum(
                            int(bit) << index
                            for index, bit in enumerate(ram_state[port])
                        ),
                        width,
                    )

        pending: Set[str] = set(self.lut_ids) | set(self.multiply_ids)
        while pending:
            progressed = False
            for instance_id in sorted(pending):
                instance = self.instances[instance_id]
                if _is_multiply_type(instance["type"]):
                    a_width = _parameter_int(instance, "A_WIDTH")
                    b_width = _parameter_int(instance, "B_WIDTH")
                    output_width = _parameter_int(instance, "Y_WIDTH")
                    left = self._bus(
                        values,
                        instance_id,
                        "a",
                        a_width,
                        overrides,
                    )
                    right = self._bus(
                        values,
                        instance_id,
                        "b",
                        b_width,
                        overrides,
                    )
                    if left is None or right is None:
                        continue
                    self._drive_bus(
                        values,
                        instance_id,
                        "out",
                        (left * right) & ((1 << output_width) - 1),
                        output_width,
                    )
                else:
                    width, input_port, output_port, truth = _lut_definition(
                        instance
                    )
                    inputs = [
                        self._pin(
                            values,
                            instance_id,
                            (
                                f"I{index}"
                                if input_port == "I"
                                else input_port
                            ),
                            bit=(0 if input_port == "I" else index),
                            overrides=overrides,
                        )
                        for index in range(width)
                    ]
                    if any(value is None for value in inputs):
                        continue
                    index = sum(
                        int(value) << offset
                        for offset, value in enumerate(inputs)
                    )
                    output = (truth >> index) & 1
                    output_net = self.output_net.get(
                        (instance_id, output_port, 0)
                    )
                    if output_net is not None:
                        values[output_net] = output
                pending.remove(instance_id)
                progressed = True
            if not progressed:
                raise ValidationError(
                    "mapped primitive simulation found unresolved "
                    f"combinational cells {sorted(pending)[:8]}"
                )

        next_state: Dict[str, Any] = {}
        for instance_id in self.ff_ids:
            instance = self.instances[instance_id]
            current = state[instance_id]
            data = self._pin(
                values, instance_id, "D", current, overrides
            )
            if instance["type"].startswith("$_DFF_"):
                next_state[instance_id] = int(data)
                continue
            data = int(data) ^ _bit(
                instance.get("parameters", {}).get("IS_D_INVERTED", 0)
            )
            enable = self._pin(
                values, instance_id, "CE", 1, overrides
            )
            if instance["type"] in {"FDRE", "FDCE"}:
                control_port = (
                    "R" if instance["type"] == "FDRE" else "CLR"
                )
                inversion_parameter = (
                    "IS_R_INVERTED"
                    if instance["type"] == "FDRE"
                    else "IS_CLR_INVERTED"
                )
                control = self._pin(
                    values, instance_id, control_port, 0, overrides
                )
                control = int(control) ^ _bit(
                    instance.get("parameters", {}).get(
                        inversion_parameter, 0
                    )
                )
                next_state[instance_id] = (
                    0 if control else int(data) if enable else current
                )
            else:
                control_port = (
                    "S" if instance["type"] == "FDSE" else "PRE"
                )
                inversion_parameter = (
                    "IS_S_INVERTED"
                    if instance["type"] == "FDSE"
                    else "IS_PRE_INVERTED"
                )
                control = self._pin(
                    values, instance_id, control_port, 0, overrides
                )
                control = int(control) ^ _bit(
                    instance.get("parameters", {}).get(
                        inversion_parameter, 0
                    )
                )
                next_state[instance_id] = (
                    1 if control else int(data) if enable else current
                )
        for instance_id in self.ram_ids:
            instance = self.instances[instance_id]
            ram_state = state[instance_id]
            contents = dict(ram_state["contents"])
            address_width = _parameter_int(instance, "ADDR_WIDTH")
            data_width = _parameter_int(instance, "DATA_WIDTH")
            word_mask = (1 << data_width) - 1
            if instance["type"] == "VTR_SP_RAM":
                address = self._bus(
                    values,
                    instance_id,
                    "addr",
                    address_width,
                    overrides,
                )
                data = self._bus(
                    values,
                    instance_id,
                    "data",
                    data_width,
                    overrides,
                )
                write_enable = self._pin(
                    values, instance_id, "we", overrides=overrides
                )
                if address is None or data is None or write_enable is None:
                    raise ValidationError(
                        f"RAM {instance_id!r} has unresolved synchronous input"
                    )
                read_word = int(contents.get(address, 0))
                if write_enable:
                    contents[address] = data & word_mask
                next_state[instance_id] = {
                    "contents": contents,
                    "out": [
                        (read_word >> bit) & 1
                        for bit in range(data_width)
                    ],
                }
            else:
                addresses = []
                data_words = []
                enables = []
                for port in (1, 2):
                    address = self._bus(
                        values,
                        instance_id,
                        f"addr{port}",
                        address_width,
                        overrides,
                    )
                    data = self._bus(
                        values,
                        instance_id,
                        f"data{port}",
                        data_width,
                        overrides,
                    )
                    enable = self._pin(
                        values,
                        instance_id,
                        f"we{port}",
                        overrides=overrides,
                    )
                    if address is None or data is None or enable is None:
                        raise ValidationError(
                            f"RAM {instance_id!r} has unresolved port {port}"
                        )
                    addresses.append(address)
                    data_words.append(data)
                    enables.append(enable)
                read_words = [
                    int(ram_state["contents"].get(address, 0))
                    for address in addresses
                ]
                for address, data, enable in zip(
                    addresses, data_words, enables
                ):
                    if enable:
                        contents[address] = data & word_mask
                next_state[instance_id] = {
                    "contents": contents,
                    **{
                        f"out{port}": [
                            (read_words[port - 1] >> bit) & 1
                            for bit in range(data_width)
                        ]
                        for port in (1, 2)
                    },
                }
        outputs = {
            f"{port}[{bit}]": values[net]
            for (port, bit), net in sorted(self.top_output_net.items())
            if net in values
        }
        return values, next_state, outputs

    def state_bit_count(self) -> int:
        return len(self.ff_ids) + sum(
            _parameter_int(self.instances[instance_id], "DATA_WIDTH")
            * (
                1
                if self.instances[instance_id]["type"] == "VTR_SP_RAM"
                else 2
            )
            for instance_id in self.ram_ids
        )

    def evaluate_lut_subset(
        self,
        instance_ids: Set[str],
        reference_values: Mapping[str, int],
        overrides: Mapping[Tuple[str, str], int],
    ) -> Dict[str, int]:
        """Evaluate one fanin-closed replica cone without resimulating the DUT."""

        unsupported = sorted(set(instance_ids) - set(self.lut_ids))
        if unsupported:
            raise ValidationError(
                "replica subset contains non-LUT instances "
                f"{unsupported[:8]}"
            )
        values: Dict[str, int] = {}
        pending = set(instance_ids)
        while pending:
            progressed = False
            for instance_id in sorted(pending):
                instance = self.instances[instance_id]
                width, input_port, output_port, truth = _lut_definition(
                    instance
                )
                inputs = []
                unresolved = False
                for index in range(width):
                    port = (
                        f"I{index}" if input_port == "I" else input_port
                    )
                    bit = 0 if input_port == "I" else index
                    net = self.input_net.get(
                        (instance_id, port, bit)
                    )
                    if net is None:
                        value = self.constants.get(
                            (instance_id, port, bit), 0
                        )
                    elif (instance_id, net) in overrides:
                        value = overrides[(instance_id, net)]
                    elif net in values:
                        value = values[net]
                    else:
                        value = reference_values.get(net)
                    if value is None:
                        unresolved = True
                        break
                    inputs.append(value)
                if unresolved:
                    continue
                address = sum(
                    int(value) << offset
                    for offset, value in enumerate(inputs)
                )
                output_net = self.output_net.get(
                    (instance_id, output_port, 0)
                )
                if output_net is not None:
                    values[output_net] = (truth >> address) & 1
                pending.remove(instance_id)
                progressed = True
            if not progressed:
                raise ValidationError(
                    "replica subset simulation found unresolved "
                    f"LUTs {sorted(pending)[:8]}"
                )
        return values


def _static_exact_equivalence_context(
    ir: EmuIR,
    assignment: Mapping[str, Any],
    schedule: Mapping[str, Any],
) -> Dict[str, Any]:
    from .combinational_cut import semantic_contract_sha256
    from .routing import static_exact_contract_from_assignment
    from .tdm import TDM_STATIC_EXACT_PROVIDER

    contract = static_exact_contract_from_assignment(assignment)
    if contract is None:
        raise ValidationError(
            "static exact macro-cycle equivalence requires an assignment "
            "semantic contract"
        )
    if schedule.get("provider") != TDM_STATIC_EXACT_PROVIDER:
        raise ValidationError(
            "static exact macro-cycle equivalence requires the dependency-"
            "aware schedule provider"
        )
    digest = semantic_contract_sha256(contract)
    if (
        schedule.get("semantic_contract") != contract
        or schedule.get("semantic_contract_sha256") != digest
    ):
        raise ValidationError(
            "static exact schedule is not bound to the assignment contract"
        )
    assignment_map = assignment.get("instance_assignment")
    if not isinstance(assignment_map, dict):
        raise ValidationError("assignment.instance_assignment must be an object")
    instance_ids = {item["id"] for item in ir.value["instances"]}
    if set(assignment_map) != instance_ids:
        raise ValidationError(
            "static exact assignment does not cover every EmuIR instance"
        )
    routes = schedule.get("routes")
    entries = schedule.get("entries")
    if not isinstance(routes, list) or not isinstance(entries, list):
        raise ValidationError("static exact schedule routes/entries must be arrays")
    if not all(
        isinstance(item, dict)
        and isinstance(item.get("net"), str)
        and isinstance(item.get("id"), str)
        for item in routes
    ):
        raise ValidationError("static exact schedule route metadata is malformed")
    route_by_net = {item["net"]: item for item in routes}
    route_by_id = {item["id"]: item for item in routes}
    if len(route_by_net) != len(routes) or len(route_by_id) != len(routes):
        raise ValidationError("static exact schedule route identities are not unique")
    cut_nodes = contract.get("cut_nodes")
    segments = contract.get("logic_segments")
    captures = contract.get("capture_requirements")
    if not all(isinstance(value, list) for value in (cut_nodes, segments, captures)):
        raise ValidationError("static exact contract arrays are malformed")
    if not all(isinstance(item, dict) for item in cut_nodes + segments + captures):
        raise ValidationError("static exact contract records must be objects")
    node_by_net = {item.get("net"): item for item in cut_nodes}
    segment_by_id = {item.get("id"): item for item in segments}
    capture_by_id = {item.get("id"): item for item in captures}
    if (
        None in node_by_net
        or None in segment_by_id
        or None in capture_by_id
        or len(node_by_net) != len(cut_nodes)
        or len(segment_by_id) != len(segments)
        or len(capture_by_id) != len(captures)
        or set(node_by_net) != set(route_by_net)
    ):
        raise ValidationError(
            "static exact contract identities/route coverage are invalid"
        )
    capture_segment_by_id: Dict[str, Mapping[str, Any]] = {}
    for segment in segments:
        if segment.get("kind") != "rx_to_capture":
            continue
        capture_id = segment.get("capture_requirement")
        if (
            not isinstance(capture_id, str)
            or capture_id not in capture_by_id
            or capture_id in capture_segment_by_id
        ):
            raise ValidationError(
                "static exact capture segment coverage is invalid"
            )
        capture_segment_by_id[capture_id] = segment
    if set(capture_segment_by_id) != set(capture_by_id):
        raise ValidationError(
            "static exact capture segment coverage is incomplete"
        )
    frame_slots = schedule.get("metrics", {}).get("frame_slots")
    commit_slot = contract.get("commit_slot")
    if (
        isinstance(frame_slots, bool)
        or not isinstance(frame_slots, int)
        or frame_slots <= 1
        or contract.get("frame_slots") != frame_slots
        or commit_slot != frame_slots - 1
    ):
        raise ValidationError("static exact frame/commit contract is invalid")
    entry_ids = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValidationError(
                f"static exact schedule entry {index} is not an object"
            )
        entry_id = entry.get("id")
        demand = entry.get("demand")
        if (
            not isinstance(entry_id, str)
            or entry_id in entry_ids
            or demand not in route_by_id
            or entry.get("net") != route_by_id[demand]["net"]
        ):
            raise ValidationError(
                f"static exact schedule entry {index} identity is invalid"
            )
        entry_ids.add(entry_id)
        for field in ("slot", "ready_slot", "arrival_slot"):
            value = entry.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValidationError(
                    f"static exact schedule entry {entry_id!r}.{field} is invalid"
                )
        if entry["slot"] >= frame_slots or entry["arrival_slot"] >= commit_slot:
            raise ValidationError(
                f"static exact schedule entry {entry_id!r} misses commit"
            )
    net_by_id = {item["id"]: item for item in ir.value["nets"]}
    if set(route_by_net) - set(net_by_id):
        raise ValidationError("static exact routes reference unknown EmuIR nets")
    return {
        "contract": contract,
        "assignment_map": assignment_map,
        "route_by_net": route_by_net,
        "route_by_id": route_by_id,
        "node_by_net": node_by_net,
        "segment_by_id": segment_by_id,
        "capture_by_id": capture_by_id,
        "capture_segment_by_id": capture_segment_by_id,
        "net_by_id": net_by_id,
        "entries": entries,
        "frame_slots": frame_slots,
        "commit_slot": commit_slot,
    }


def _static_exact_sink_overrides(
    context: Mapping[str, Any],
    shadow_values: Mapping[Tuple[str, str], int],
) -> Dict[Tuple[str, str], int]:
    overrides: Dict[Tuple[str, str], int] = {}
    assignment_map = context["assignment_map"]
    for net_id, route in context["route_by_net"].items():
        demand = route["id"]
        source = route["source"]
        sinks = set(route["sinks"])
        for endpoint in context["net_by_id"][net_id]["sinks"]:
            instance_id = endpoint["instance"]
            if instance_id is None:
                continue
            fpga = assignment_map[instance_id]
            if fpga == source:
                continue
            if fpga not in sinks:
                raise ValidationError(
                    f"static exact cut {net_id!r} omits sink FPGA {fpga!r}"
                )
            # An unavailable current-frame shadow is deliberately forced to a
            # local reset value.  This prevents the monolithic evaluator from
            # creating a hidden cross-FPGA bypass; readiness checks below must
            # prove that no TX/capture consumes this placeholder.
            overrides[(instance_id, net_id)] = int(
                shadow_values.get((demand, fpga), 0)
            )
    return overrides


def _static_exact_source_ready_slot(
    node: Mapping[str, Any],
    segment_by_id: Mapping[str, Mapping[str, Any]],
    current_arrivals: Mapping[Tuple[str, str], int],
) -> Tuple[int, List[Dict[str, Any]]]:
    evidence = []
    predecessor_coverage = []
    source_fpgas = node.get("source_fpgas")
    if not isinstance(source_fpgas, list) or len(source_fpgas) != 1:
        raise ValidationError(
            f"static exact cut {node.get('net')!r} has invalid source FPGA"
        )
    for segment_id in node.get("source_segment_ids", []):
        segment = segment_by_id.get(segment_id)
        if segment is None:
            raise ValidationError(
                f"static exact cut {node.get('net')!r} references unknown "
                f"segment {segment_id!r}"
            )
        budget = segment.get("budget_slots")
        if (
            isinstance(budget, bool)
            or not isinstance(budget, int)
            or budget < 0
        ):
            raise ValidationError(
                f"static exact segment {segment_id!r} has invalid budget"
            )
        if segment.get("kind") == "launch_to_tx":
            if segment.get("sink_cut_net") != node.get("net"):
                raise ValidationError(
                    f"static exact launch segment {segment_id!r} is misbound"
                )
            evidence.append(
                {
                    "segment": segment_id,
                    "kind": "launch_to_tx",
                    "ready_slot": budget,
                }
            )
            continue
        if segment.get("kind") != "rx_to_tx":
            raise ValidationError(
                f"static exact source segment {segment_id!r} has invalid kind"
            )
        predecessor = segment.get("source_cut_net")
        key = (predecessor, source_fpgas[0])
        if (
            segment.get("sink_cut_net") != node.get("net")
            or segment.get("fpga") != source_fpgas[0]
            or predecessor not in node.get("predecessor_cut_nets", [])
            or key not in current_arrivals
        ):
            raise ValidationError(
                f"static exact dependency {segment_id!r} is not ready in the "
                "current macro-cycle"
            )
        arrival = current_arrivals[key]
        predecessor_coverage.append(predecessor)
        evidence.append(
            {
                "segment": segment_id,
                "kind": "rx_to_tx",
                "predecessor_cut_net": predecessor,
                "arrival_slot": arrival,
                "ready_slot": arrival + budget,
            }
        )
    if not evidence:
        raise ValidationError(
            f"static exact cut {node.get('net')!r} has no readiness evidence"
        )
    if sorted(predecessor_coverage) != sorted(
        node.get("predecessor_cut_nets", [])
    ):
        raise ValidationError(
            f"static exact cut {node.get('net')!r} predecessor coverage is "
            "incomplete"
        )
    return max(item["ready_slot"] for item in evidence), evidence


def _simulate_static_exact_macro_step(
    model: _MappedModel,
    context: Mapping[str, Any],
    state: Mapping[str, Any],
    cycle: int,
    seed: int,
    shadow_values: Mapping[Tuple[str, str], int],
    input_values: Optional[Mapping[Tuple[str, int], int]] = None,
) -> Dict[str, Any]:
    reference_values, reference_next, reference_outputs = model.evaluate(
        state,
        cycle,
        seed,
        input_values=input_values,
    )
    del reference_values  # TX must never consume the reference final-net map.
    shadows = dict(shadow_values)
    current_arrivals: Dict[Tuple[str, str], int] = {}
    current_shadow_generation: Set[Tuple[str, str]] = set()
    arrivals_by_slot: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    entries_by_slot: Dict[int, List[Mapping[str, Any]]] = defaultdict(list)
    for entry in context["entries"]:
        entries_by_slot[entry["slot"]].append(entry)
    tx_samples = []
    source_ready_checks = 0
    relay_ready_checks = 0
    uninitialized_shadow_reads = 0
    for slot in range(context["frame_slots"]):
        source_value_cache: Dict[str, int] = {}
        for entry in sorted(
            entries_by_slot.get(slot, []),
            key=lambda item: (item["hop"], item["id"]),
        ):
            route = context["route_by_id"][entry["demand"]]
            if entry["from"] == route["source"]:
                node = context["node_by_net"][entry["net"]]
                ready_slot, readiness = _static_exact_source_ready_slot(
                    node,
                    context["segment_by_id"],
                    current_arrivals,
                )
                source_ready_checks += 1
                if entry["ready_slot"] != ready_slot or slot < ready_slot:
                    raise ValidationError(
                        f"static exact TX {entry['id']!r} samples net "
                        f"{entry['net']!r} at slot {slot}, before source-ready "
                        f"slot {ready_slot}"
                    )
                if entry["net"] not in source_value_cache:
                    overrides = _static_exact_sink_overrides(context, shadows)
                    local_values, _, _ = model.evaluate(
                        state,
                        cycle,
                        seed,
                        overrides=overrides,
                        input_values=input_values,
                    )
                    if entry["net"] not in local_values:
                        raise ValidationError(
                            f"static exact TX source {entry['net']!r} is "
                            "unresolved"
                        )
                    source_value_cache[entry["net"]] = local_values[
                        entry["net"]
                    ]
                value = source_value_cache[entry["net"]]
                evidence = readiness
            else:
                key = (entry["demand"], entry["from"])
                logical_key = (entry["net"], entry["from"])
                if (
                    key not in current_shadow_generation
                    or logical_key not in current_arrivals
                ):
                    uninitialized_shadow_reads += 1
                    raise ValidationError(
                        f"static exact relay {entry['id']!r} consumes a stale "
                        "or unavailable shadow"
                    )
                ready_slot = current_arrivals[logical_key] + 1
                relay_ready_checks += 1
                if entry["ready_slot"] != ready_slot or slot < ready_slot:
                    raise ValidationError(
                        f"static exact relay {entry['id']!r} samples before "
                        f"ready slot {ready_slot}"
                    )
                value = shadows[key]
                evidence = [
                    {
                        "kind": "route_tree_relay",
                        "arrival_slot": current_arrivals[logical_key],
                        "ready_slot": ready_slot,
                    }
                ]
            arrivals_by_slot[entry["arrival_slot"]].append(
                {
                    "entry": entry,
                    "value": int(value),
                }
            )
            tx_samples.append(
                {
                    "entry": entry["id"],
                    "net": entry["net"],
                    "fpga": entry["from"],
                    "slot": slot,
                    "value": int(value),
                    "readiness": evidence,
                }
            )
        # TX samples the pre-edge shadow state. RX nonblocking updates at the
        # same labelled edge become visible only after all TX samples here.
        for event in sorted(
            arrivals_by_slot.pop(slot, []),
            key=lambda item: item["entry"]["id"],
        ):
            entry = event["entry"]
            shadow_key = (entry["demand"], entry["to"])
            if shadow_key in current_shadow_generation:
                raise ValidationError(
                    f"static exact demand {entry['demand']!r} has multiple "
                    f"current-frame arrivals at {entry['to']!r}"
                )
            shadows[shadow_key] = event["value"]
            current_shadow_generation.add(shadow_key)
            current_arrivals[(entry["net"], entry["to"])] = slot
    if arrivals_by_slot:
        raise ValidationError("static exact schedule has arrivals after frame end")

    capture_checks = 0
    for capture_id, capture in sorted(context["capture_by_id"].items()):
        segment = context["capture_segment_by_id"][capture_id]
        key = (capture.get("cut_net"), capture.get("fpga"))
        if key not in current_arrivals:
            raise ValidationError(
                f"static exact capture {capture_id!r} consumes no current-"
                "frame arrival"
            )
        ready_slot = current_arrivals[key] + segment["budget_slots"]
        if ready_slot > context["commit_slot"]:
            raise ValidationError(
                f"static exact capture {capture_id!r} is ready at "
                f"{ready_slot}, after commit {context['commit_slot']}"
            )
        capture_checks += 1

    final_overrides = _static_exact_sink_overrides(context, shadows)
    _, partition_next, partition_outputs = model.evaluate(
        state,
        cycle,
        seed,
        overrides=final_overrides,
        input_values=input_values,
    )
    if partition_next != reference_next:
        mismatch = next(
            instance_id
            for instance_id in sorted(reference_next)
            if partition_next.get(instance_id) != reference_next[instance_id]
        )
        raise ValidationError(
            f"macro-cycle {cycle}: static exact partition state mismatch at "
            f"{mismatch!r}"
        )
    if partition_outputs != reference_outputs:
        raise ValidationError(
            f"macro-cycle {cycle}: static exact partition top-output mismatch"
        )
    return {
        "next_state": reference_next,
        "outputs": reference_outputs,
        "shadow_values": shadows,
        "tx_samples": tx_samples,
        "source_ready_checks": source_ready_checks,
        "relay_ready_checks": relay_ready_checks,
        "capture_checks": capture_checks,
        "uninitialized_shadow_reads": uninitialized_shadow_reads,
    }


def simulate_static_exact_partition_equivalence(
    ir: EmuIR,
    assignment: Mapping[str, Any],
    schedule: Mapping[str, Any],
    cycles: int = 16,
    seed: int = 20260727,
) -> Dict[str, Any]:
    """Random-trace exact-cut equivalence with slot-accurate local TX values."""
    if isinstance(cycles, bool) or not isinstance(cycles, int) or cycles <= 0:
        raise ValidationError("equivalence cycles must be positive")
    model = _MappedModel(ir)
    context = _static_exact_equivalence_context(ir, assignment, schedule)
    state = model.initial_state()
    shadow_values: Dict[Tuple[str, str], int] = {}
    trace = hashlib.sha256()
    tx_samples = 0
    source_ready_checks = 0
    relay_ready_checks = 0
    capture_checks = 0
    uninitialized_shadow_reads = 0
    compared_outputs = 0
    compared_state_bits = 0
    # Start after the deterministic reset stimulus interval. Shadow validity
    # is still empty, so the first post-reset frame proves it consumes only
    # current-frame arrivals rather than reset/stale implementation state.
    for macro_cycle in range(cycles):
        stimulus_cycle = macro_cycle + 3
        result = _simulate_static_exact_macro_step(
            model,
            context,
            state,
            stimulus_cycle,
            seed,
            shadow_values,
        )
        state = result["next_state"]
        shadow_values = result["shadow_values"]
        tx_samples += len(result["tx_samples"])
        source_ready_checks += result["source_ready_checks"]
        relay_ready_checks += result["relay_ready_checks"]
        capture_checks += result["capture_checks"]
        uninitialized_shadow_reads += result["uninitialized_shadow_reads"]
        compared_outputs += len(result["outputs"])
        compared_state_bits += model.state_bit_count()
        trace.update(
            json.dumps(
                {
                    "cycle": macro_cycle,
                    "next_state": result["next_state"],
                    "outputs": result["outputs"],
                    "tx_samples": result["tx_samples"],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    return {
        "status": "pass",
        "provider": "static-exact-event-driven-macro-cycle-model-v1",
        "evidence_type": "random-simulation",
        "qualification": "randomized-trace-validation-not-proof",
        "cycles": cycles,
        "seed": seed,
        "primitive_instances": len(model.instances),
        "flip_flops": len(model.ff_ids),
        "luts": len(model.lut_ids),
        "multipliers": len(model.multiply_ids),
        "memory_macros": len(model.ram_ids),
        "tx_samples": tx_samples,
        "source_ready_checks": source_ready_checks,
        "relay_ready_checks": relay_ready_checks,
        "capture_checks": capture_checks,
        "startup_uninitialized_shadow_reads": uninitialized_shadow_reads,
        "compared_state_bits": compared_state_bits,
        "compared_output_bits": compared_outputs,
        "mismatches": 0,
        "trace_sha256": trace.hexdigest(),
    }


def exhaustively_verify_static_exact_partition_equivalence(
    ir: EmuIR,
    assignment: Mapping[str, Any],
    schedule: Mapping[str, Any],
    *,
    max_variables: int = 12,
) -> Dict[str, Any]:
    """Exhaust every FF state and non-clock primary input for a small model."""
    if (
        isinstance(max_variables, bool)
        or not isinstance(max_variables, int)
        or max_variables <= 0
    ):
        raise ValidationError("exhaustive max_variables must be positive")
    model = _MappedModel(ir)
    if model.ram_ids:
        raise ValidationError(
            "exhaustive static exact proof does not support memory state"
        )
    context = _static_exact_equivalence_context(ir, assignment, schedule)
    reset_values = {
        key: value
        for key in model.top_input_net
        if (value := _reset_deasserted_value(key[0])) is not None
    }
    input_keys = sorted(
        key
        for key in model.top_input_net
        if key[0].lower() not in {"clk", "clock"}
        and key not in reset_values
    )
    variables = len(model.ff_ids) + len(input_keys)
    if variables > max_variables:
        raise ValidationError(
            "exhaustive static exact proof variable limit exceeded: "
            f"{variables} > {max_variables}"
        )
    cases = 1 << variables
    trace = hashlib.sha256()
    source_ready_checks = 0
    relay_ready_checks = 0
    capture_checks = 0
    tx_samples = 0
    for vector in range(cases):
        bits = [(vector >> index) & 1 for index in range(variables)]
        state = {
            instance_id: bits[index]
            for index, instance_id in enumerate(model.ff_ids)
        }
        input_values = {
            key: bits[len(model.ff_ids) + index]
            for index, key in enumerate(input_keys)
        }
        input_values.update(reset_values)
        result = _simulate_static_exact_macro_step(
            model,
            context,
            state,
            0,
            0,
            {},
            input_values=input_values,
        )
        source_ready_checks += result["source_ready_checks"]
        relay_ready_checks += result["relay_ready_checks"]
        capture_checks += result["capture_checks"]
        tx_samples += len(result["tx_samples"])
        trace.update(
            json.dumps(
                {
                    "state": state,
                    "inputs": {
                        f"{port}[{bit}]": value
                        for (port, bit), value in input_values.items()
                    },
                    "next_state": result["next_state"],
                    "outputs": result["outputs"],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    return {
        "status": "pass",
        "provider": "static-exact-exhaustive-macro-step-v1",
        "evidence_type": "exhaustive-small-model",
        "qualification": "complete-enumeration-proof-for-declared-model",
        "state_bits": len(model.ff_ids),
        "primary_input_bits": len(input_keys),
        "constrained_reset_bits": len(reset_values),
        "assumptions": [
            "one-commit macro-step semantics",
            "reset inputs constrained to their deasserted values",
            "transport shadows begin unavailable and must be produced in-frame",
        ],
        "variables": variables,
        "cases": cases,
        "source_ready_checks": source_ready_checks,
        "relay_ready_checks": relay_ready_checks,
        "capture_checks": capture_checks,
        "tx_samples": tx_samples,
        "mismatches": 0,
        "trace_sha256": trace.hexdigest(),
    }


def simulate_partition_equivalence(
    ir: EmuIR,
    assignment: Mapping[str, Any],
    schedule: Mapping[str, Any],
    cycles: int = 16,
    seed: int = 20260727,
) -> Dict[str, Any]:
    if cycles <= 0:
        raise ValidationError("equivalence cycles must be positive")
    model = _MappedModel(ir)
    assignment_map = assignment["instance_assignment"]
    cut_class_by_net = {
        cut["net"]: cut["cut_class"] for cut in assignment["cut_nets"]
    }
    route_source = {}
    for cut in assignment["cut_nets"]:
        route_source[cut["net"]] = cut["source_fpgas"][0]

    route_by_net = {
        route["net"]: route for route in schedule.get("routes", [])
    }
    replica_records = assignment.get("replication", {}).get("replicas", [])
    output_nets_by_instance: Dict[str, Set[str]] = {
        instance_id: set() for instance_id in model.instances
    }
    sink_nets_by_instance: Dict[str, Set[str]] = {
        instance_id: set() for instance_id in model.instances
    }
    for net in ir.value["nets"]:
        for endpoint in net["drivers"]:
            if endpoint["instance"] is not None:
                output_nets_by_instance[endpoint["instance"]].add(net["id"])
        for endpoint in net["sinks"]:
            if endpoint["instance"] is not None:
                sink_nets_by_instance[endpoint["instance"]].add(net["id"])
    first_source_slot: Dict[str, int] = {}
    completion_by_round: Dict[int, int] = {}
    for entry in schedule.get("entries", []):
        if entry["from"] == route_source[entry["net"]]:
            first_source_slot[entry["net"]] = min(
                entry["slot"],
                first_source_slot.get(entry["net"], entry["slot"]),
            )
        transport_round = route_by_net[entry["net"]].get(
            "transport_round", 0
        )
        completion_by_round[transport_round] = max(
            entry["arrival_slot"],
            completion_by_round.get(
                transport_round, entry["arrival_slot"]
            ),
        )
    round_barrier_checks = 0
    for net_id, route in route_by_net.items():
        transport_round = route.get("transport_round", 0)
        prior_completions = [
            completion
            for round_index, completion in completion_by_round.items()
            if round_index < transport_round
        ]
        if not prior_completions:
            continue
        round_barrier_checks += 1
        required_slot = max(prior_completions) + 1
        source_slot = first_source_slot.get(net_id)
        if source_slot is None or source_slot < required_slot:
            raise ValidationError(
                f"cut net {net_id!r} in transport round "
                f"{transport_round} is sent at {source_slot!r}, before "
                f"round barrier slot {required_slot}"
            )

    state = model.initial_state()
    trace = hashlib.sha256()
    compared_outputs = 0
    compared_state_bits = 0
    compared_replica_outputs = 0
    for cycle in range(cycles):
        reference_values, reference_next, reference_outputs = model.evaluate(
            state, cycle, seed
        )
        shadow: Dict[Tuple[str, str], int] = {}
        for entry in sorted(
            schedule["entries"],
            key=lambda item: (
                item["slot"],
                item["hop"],
                item["arrival_slot"],
                item["id"],
            ),
        ):
            key = (entry["demand"], entry["from"])
            if entry["from"] == route_source[entry["net"]]:
                value = reference_values[entry["net"]]
            else:
                if key not in shadow:
                    raise ValidationError(
                        f"schedule consumes unavailable shadow value {key}"
                    )
                value = shadow[key]
            shadow[(entry["demand"], entry["to"])] = value

        overrides: Dict[Tuple[str, str], int] = {}
        demand_by_net = {
            route["net"]: route["id"] for route in schedule["routes"]
        }
        for net in ir.value["nets"]:
            demand = demand_by_net.get(net["id"])
            if demand is None:
                continue
            source_fpga = route_source[net["id"]]
            for endpoint in net["sinks"]:
                instance_id = endpoint["instance"]
                if instance_id is None:
                    continue
                fpga_id = assignment_map[instance_id]
                if fpga_id in route_by_net[net["id"]]["sinks"]:
                    key = (demand, fpga_id)
                    if key not in shadow:
                        raise ValidationError(
                            f"cut sink {instance_id!r} lacks shadow {key}"
                        )
                    overrides[(instance_id, net["id"])] = shadow[key]

        _, partition_next, partition_outputs = model.evaluate(
            state, cycle, seed, overrides=overrides
        )
        for record in replica_records:
            target = record["target_fpga"]
            members = {
                item["original_instance"] for item in record["instances"]
            }
            replica_overrides: Dict[Tuple[str, str], int] = {}
            for instance_id in members:
                for net_id in sink_nets_by_instance[instance_id]:
                    route = route_by_net.get(net_id)
                    if route is None or target not in route["sinks"]:
                        continue
                    demand = route["id"]
                    key = (demand, target)
                    if key not in shadow:
                        raise ValidationError(
                            f"replica {record['cluster']!r} at {target!r} "
                            f"lacks shadow {key}"
                        )
                    replica_overrides[(instance_id, net_id)] = shadow[key]
            replica_values = model.evaluate_lut_subset(
                members,
                reference_values,
                replica_overrides,
            )
            output_nets = {
                net_id
                for instance_id in members
                for net_id in output_nets_by_instance[instance_id]
            }
            for net_id in output_nets:
                if replica_values.get(net_id) != reference_values.get(net_id):
                    raise ValidationError(
                        f"cycle {cycle}: replica {record['cluster']!r} at "
                        f"{target!r} mismatches net {net_id!r}"
                    )
                compared_replica_outputs += 1
        if partition_next != reference_next:
            mismatch = next(
                instance_id
                for instance_id in sorted(reference_next)
                if partition_next.get(instance_id)
                != reference_next[instance_id]
            )
            raise ValidationError(
                f"cycle {cycle}: partition state mismatch at {mismatch!r}"
            )
        if partition_outputs != reference_outputs:
            raise ValidationError(
                f"cycle {cycle}: partition top-output mismatch"
            )
        compared_outputs += len(reference_outputs)
        compared_state_bits += model.state_bit_count()
        trace.update(
            (
                f"{cycle}:"
                + json.dumps(reference_next, sort_keys=True)
                + ":"
                + "".join(
                    str(reference_outputs[item])
                    for item in sorted(reference_outputs)
                )
            ).encode("utf-8")
        )
        state = reference_next

    return {
        "status": "pass",
        "provider": "generic-lut-ff-vtr-hard-block-cycle-model-v3",
        "cycles": cycles,
        "seed": seed,
        "primitive_instances": len(model.instances),
        "flip_flops": len(model.ff_ids),
        "luts": len(model.lut_ids),
        "multipliers": len(model.multiply_ids),
        "memory_macros": len(model.ram_ids),
        "memory_semantics": "synchronous-read-old-zero-initial-sparse",
        "register_input_cuts": sum(
            cut_class == "register_input"
            for cut_class in cut_class_by_net.values()
        ),
        "transport_rounds": len(completion_by_round),
        "round_barrier_checks": round_barrier_checks,
        "compared_state_bits": compared_state_bits,
        "compared_output_bits": compared_outputs,
        "replica_copies": len(replica_records),
        "compared_replica_output_bits": compared_replica_outputs,
        "mismatches": 0,
        "trace_sha256": trace.hexdigest(),
    }
