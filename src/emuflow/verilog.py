import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from .errors import ValidationError
from .io import write_json
from .ir import EmuIR


MAPPED_VERILOG_REPORT_SCHEMA = "emuflow.mapped-verilog-report/v1"


def _identifier(value: str) -> str:
    return f"\\{value} "


def _parameter(value: Any) -> str:
    text = str(value)
    if text and all(character.lower() in "01xz" for character in text):
        return f"{len(text)}'b{text}"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(text)


def _emittable_parameters(instance: Mapping[str, Any]) -> Dict[str, Any]:
    parameters = dict(instance.get("parameters", {}))
    init = parameters.get("INIT")
    if (
        isinstance(init, str)
        and init
        and all(character.lower() in "01xz" for character in init)
        and any(character.lower() in "xz" for character in init)
    ):
        # FD* INIT accepts only a known bit in Vivado. An x/z value means the
        # source design did not specify a hardware power-up value; omitting
        # the property preserves that unspecified contract and lets the
        # primitive use its legal default instead of emitting invalid RTL.
        parameters.pop("INIT")
    return parameters


def mapped_verilog(ir: EmuIR, *, timing_only: bool = False) -> str:
    net_wire = {
        net["id"]: f"__emuflow_net_{index}"
        for index, net in enumerate(ir.value["nets"])
    }
    pin_net: Dict[Tuple[str, str, int], str] = {}
    pins_by_instance: Dict[str, set[Tuple[str, int]]] = {}
    for net in ir.value["nets"]:
        for collection in ("drivers", "sinks"):
            for endpoint in net[collection]:
                if endpoint["instance"] is not None:
                    instance_id = endpoint["instance"]
                    port_bit = (endpoint["port"], endpoint["bit"])
                    pin_net[(instance_id, *port_bit)] = net_wire[net["id"]]
                    pins_by_instance.setdefault(instance_id, set()).add(
                        port_bit
                    )
    constants: Dict[Tuple[str, str, int], Any] = {}
    for instance in ir.value["instances"]:
        instance_id = instance["id"]
        for item in instance.get("constant_connections", []):
            port_bit = (item["port"], item["bit"])
            constants[(instance_id, *port_bit)] = item["value"]
            pins_by_instance.setdefault(instance_id, set()).add(port_bit)

    lines = [
        f"module {_identifier(ir.value['design']['top'])}(",
        "  "
        + ",\n  ".join(
            _identifier(port["id"]) for port in ir.value["ports"]
        ),
        ");",
    ]
    for port in ir.value["ports"]:
        width = "" if port["width"] == 1 else f"[{port['width'] - 1}:0] "
        direction = port["direction"]
        if direction not in {"input", "output", "inout"}:
            raise ValidationError(
                f"cannot emit unknown-direction port {port['id']!r}"
            )
        wire_keyword = "" if timing_only else "wire "
        lines.append(
            f"  {direction} {wire_keyword}{width}{_identifier(port['id'])};"
        )
    lines.append("")
    for wire in net_wire.values():
        lines.append(f"  wire {wire};")
    lines.append("")

    for net in ir.value["nets"]:
        wire = net_wire[net["id"]]
        for endpoint in net["drivers"]:
            if endpoint["instance"] is None:
                port = _identifier(endpoint["port"])
                select = (
                    ""
                    if next(
                        item["width"]
                        for item in ir.value["ports"]
                        if item["id"] == endpoint["port"]
                    )
                    == 1
                    else f"[{endpoint['bit']}]"
                )
                lines.append(f"  assign {wire} = {port}{select};")
        for endpoint in net["sinks"]:
            if endpoint["instance"] is None:
                port = _identifier(endpoint["port"])
                select = (
                    ""
                    if next(
                        item["width"]
                        for item in ir.value["ports"]
                        if item["id"] == endpoint["port"]
                    )
                    == 1
                    else f"[{endpoint['bit']}]"
                )
                lines.append(f"  assign {port}{select} = {wire};")
    lines.append("")

    for instance in ir.value["instances"]:
        parameters = _emittable_parameters(instance)
        parameter_text = ""
        if parameters and not timing_only:
            parameter_text = " #(" + ", ".join(
                f".{_identifier(name)}({_parameter(value)})"
                for name, value in sorted(parameters.items())
            ) + ")"
        pins = pins_by_instance.get(instance["id"], set())
        connections = []
        for port, bit in sorted(pins):
            if bit != 0:
                raise ValidationError(
                    f"multi-bit primitive pin unsupported: "
                    f"{instance['id']}.{port}[{bit}]"
                )
            expression = pin_net.get((instance["id"], port, bit))
            if expression is None:
                expression = f"1'b{constants[(instance['id'], port, bit)]}"
            connections.append(
                f".{_identifier(port)}({expression})"
            )
        if not timing_only:
            lines.append('  (* KEEP = "yes", DONT_TOUCH = "yes" *)')
        lines.extend(
            [
                f"  {_identifier(instance['type'])}{parameter_text} "
                f"{_identifier(instance['id'])}(",
                "    " + ",\n    ".join(connections),
                "  );",
            ]
        )
    lines.extend(["endmodule", ""])
    return "\n".join(lines)


def emit_mapped_verilog(
    ir_path: Path,
    output_path: Path,
    report_path: Optional[Path] = None,
) -> Mapping[str, Any]:
    ir = EmuIR.load(ir_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(mapped_verilog(ir), encoding="utf-8")
    report = {
        "schema": MAPPED_VERILOG_REPORT_SCHEMA,
        "status": "pass",
        "design": ir.value["design"]["name"],
        "top": ir.value["design"]["top"],
        "instances": len(ir.value["instances"]),
        "nets": len(ir.value["nets"]),
        "ports": len(ir.value["ports"]),
        "omitted_unknown_init_parameters": sum(
            "INIT" in instance.get("parameters", {})
            and "INIT" not in _emittable_parameters(instance)
            for instance in ir.value["instances"]
        ),
        "output": str(output_path),
    }
    if report_path is not None:
        write_json(report_path, report)
    return report
