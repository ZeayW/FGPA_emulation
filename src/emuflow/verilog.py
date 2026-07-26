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


def mapped_verilog(ir: EmuIR) -> str:
    net_wire = {
        net["id"]: f"__emuflow_net_{index}"
        for index, net in enumerate(ir.value["nets"])
    }
    pin_net: Dict[Tuple[str, str, int], str] = {}
    for net in ir.value["nets"]:
        for collection in ("drivers", "sinks"):
            for endpoint in net[collection]:
                if endpoint["instance"] is not None:
                    pin_net[
                        (
                            endpoint["instance"],
                            endpoint["port"],
                            endpoint["bit"],
                        )
                    ] = net_wire[net["id"]]
    constants = {
        (instance["id"], item["port"], item["bit"]): item["value"]
        for instance in ir.value["instances"]
        for item in instance.get("constant_connections", [])
    }

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
        lines.append(
            f"  {direction} wire {width}{_identifier(port['id'])};"
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
        parameters = instance.get("parameters", {})
        parameter_text = ""
        if parameters:
            parameter_text = " #(" + ", ".join(
                f".{_identifier(name)}({_parameter(value)})"
                for name, value in sorted(parameters.items())
            ) + ")"
        pins = {
            (port, bit)
            for instance_id, port, bit in pin_net
            if instance_id == instance["id"]
        }
        pins.update(
            (port, bit)
            for instance_id, port, bit in constants
            if instance_id == instance["id"]
        )
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
        lines.extend(
            [
                '  (* KEEP = "yes", DONT_TOUCH = "yes" *)',
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
        "output": str(output_path),
    }
    if report_path is not None:
        write_json(report_path, report)
    return report
