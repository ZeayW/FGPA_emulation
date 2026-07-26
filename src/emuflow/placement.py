from collections import defaultdict, deque
from pathlib import Path
from typing import (
    Any,
    Deque,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Set,
    Tuple,
)

from .architecture import ArchitectureDB
from .errors import ImportError, ValidationError
from .io import read_json
from .ir import EmuIR
from .openparf import openparf_instance_names


PLACEMENT_SCHEMA = "emuflow.placement/v1"


def _vivado_mapped_name(value: str) -> str:
    """Return the exact NAME property Vivado exposes for a Yosys identifier."""
    return value.replace("\\", "\\\\")


def _vivado_regexp_literal(value: str) -> str:
    """Encode a Yosys mapped name as Vivado exposes it to get_cells."""
    # Vivado preserves Yosys escaped identifiers but exposes every embedded
    # backslash as two characters in the NAME property.
    vivado_name = _vivado_mapped_name(value)
    return "".join(f"\\x{byte:02x}" for byte in vivado_name.encode("utf-8"))


class Placement:
    def __init__(
        self,
        value: Mapping[str, Any],
        architecture: ArchitectureDB,
        ir: Optional[EmuIR] = None,
    ):
        self.value = dict(value)
        self.architecture = architecture
        self.ir = ir
        self.validate()

    @classmethod
    def load(
        cls,
        path: Path,
        architecture: ArchitectureDB,
        ir: Optional[EmuIR] = None,
    ) -> "Placement":
        return cls(read_json(path), architecture, ir)

    @classmethod
    def from_openparf_pl(
        cls,
        path: Path,
        architecture: ArchitectureDB,
        ir: EmuIR,
    ) -> "Placement":
        cell_types = {
            instance["id"]: instance["type"] for instance in ir.value["instances"]
        }
        safe_to_original = {
            safe: original
            for original, safe in openparf_instance_names(ir).items()
        }
        cells: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        with path.open("r", encoding="utf-8") as stream:
            for line_number, raw_line in enumerate(stream, start=1):
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                fields = line.split()
                if len(fields) not in {4, 5}:
                    raise ImportError(
                        f"{path}:{line_number}: expected 'instance x y z [FIXED]'"
                    )
                raw_instance = fields[0]
                instance = safe_to_original.get(raw_instance, raw_instance)
                if instance not in cell_types:
                    raise ImportError(
                        f"{path}:{line_number}: unknown instance {raw_instance!r}"
                    )
                if instance in seen:
                    raise ImportError(
                        f"{path}:{line_number}: duplicate instance {instance!r}"
                    )
                seen.add(instance)
                try:
                    x, y, z = (int(field) for field in fields[1:4])
                except ValueError as error:
                    raise ImportError(
                        f"{path}:{line_number}: x, y and z must be integers"
                    ) from error
                site = architecture.site_at(x, y)
                if site is None:
                    raise ImportError(
                        f"{path}:{line_number}: no architecture site at ({x}, {y})"
                    )
                cell_type = cell_types[instance]
                bel = architecture.legal_bel(site, cell_type, z)
                if bel is None:
                    raise ImportError(
                        f"{path}:{line_number}: {cell_type} has no unambiguous "
                        f"legal BEL at {site['name']} z={z}"
                    )
                cells.append(
                    {
                        "instance": instance,
                        "cell_type": cell_type,
                        "site": site["name"],
                        "bel": bel["name"],
                        "x": x,
                        "y": y,
                        "z": z,
                        "fixed": len(fields) == 5 and fields[4].upper() == "FIXED",
                    }
                )
        return cls(
            {
                "schema": PLACEMENT_SCHEMA,
                "part": architecture.part,
                "source": {
                    "format": "openparf-bookshelf-pl",
                    "path": str(path),
                },
                "cells": sorted(cells, key=lambda cell: cell["instance"]),
            },
            architecture,
            ir,
        )

    @classmethod
    def greedy_reference(
        cls, architecture: ArchitectureDB, ir: EmuIR
    ) -> "Placement":
        # Index each physical slot once by its compatibility signature. The
        # original reference implementation rescanned every site for every
        # cell, which was acceptable for smoke tests but quadratic at 100k+
        # cells.
        slots: Dict[
            Tuple[str, ...],
            Deque[Tuple[Dict[str, Any], Dict[str, Any]]],
        ] = defaultdict(deque)
        signatures_by_type: Dict[str, List[Tuple[str, ...]]] = defaultdict(list)
        for site in architecture.sites:
            for bel in site["bels"]:
                signature = tuple(sorted(bel["compatible_cells"]))
                slots[signature].append((site, bel))
                for cell_type in signature:
                    if signature not in signatures_by_type[cell_type]:
                        signatures_by_type[cell_type].append(signature)

        cells: List[Dict[str, Any]] = []
        for instance in sorted(ir.value["instances"], key=lambda item: item["id"]):
            selected: Optional[Tuple[Dict[str, Any], Dict[str, Any]]] = None
            for signature in signatures_by_type.get(instance["type"], []):
                if slots[signature]:
                    selected = slots[signature].popleft()
                    break
            if selected is None:
                raise ValidationError(
                    f"no legal ArchitectureDB slot remains for "
                    f"{instance['id']!r} ({instance['type']})"
                )
            site, bel = selected
            cells.append(
                {
                    "instance": instance["id"],
                    "cell_type": instance["type"],
                    "site": site["name"],
                    "bel": bel["name"],
                    "x": site["x"],
                    "y": site["y"],
                    "z": bel["z"],
                    "fixed": False,
                }
            )
        return cls(
            {
                "schema": PLACEMENT_SCHEMA,
                "part": architecture.part,
                "source": {"format": "emuflow-greedy-reference/v1"},
                "cells": cells,
            },
            architecture,
            ir,
        )

    def validate(self) -> None:
        value = self.value
        if value.get("schema") != PLACEMENT_SCHEMA:
            raise ValidationError(
                f"placement.schema: expected {PLACEMENT_SCHEMA!r}, "
                f"got {value.get('schema')!r}"
            )
        if value.get("part") != self.architecture.part:
            raise ValidationError(
                f"placement.part: expected {self.architecture.part!r}, "
                f"got {value.get('part')!r}"
            )
        if not isinstance(value.get("source"), dict):
            raise ValidationError("placement.source: expected an object")
        cells = value.get("cells")
        if not isinstance(cells, list):
            raise ValidationError("placement.cells: expected an array")

        expected_types = (
            {
                instance["id"]: instance["type"]
                for instance in self.ir.value["instances"]
            }
            if self.ir is not None
            else None
        )
        seen_instances: Set[str] = set()
        occupied: Set[Tuple[str, str]] = set()
        for index, cell in enumerate(cells):
            context = f"placement.cells[{index}]"
            if not isinstance(cell, dict):
                raise ValidationError(f"{context}: expected an object")
            for key in ("instance", "cell_type", "site", "bel"):
                if not isinstance(cell.get(key), str) or not cell[key]:
                    raise ValidationError(
                        f"{context}.{key}: expected a non-empty string"
                    )
            instance = cell["instance"]
            if instance in seen_instances:
                raise ValidationError(
                    f"{context}.instance: duplicate {instance!r}"
                )
            seen_instances.add(instance)
            if expected_types is not None:
                if instance not in expected_types:
                    raise ValidationError(
                        f"{context}.instance: unknown instance {instance!r}"
                    )
                if cell["cell_type"] != expected_types[instance]:
                    raise ValidationError(
                        f"{context}.cell_type: expected "
                        f"{expected_types[instance]!r}"
                    )
            site = self.architecture.site_named(cell["site"])
            if site is None:
                raise ValidationError(
                    f"{context}.site: unknown site {cell['site']!r}"
                )
            if (cell.get("x"), cell.get("y")) != (site["x"], site["y"]):
                raise ValidationError(
                    f"{context}: coordinate does not match site {site['name']}"
                )
            matching_bels = [
                bel
                for bel in site["bels"]
                if bel["name"] == cell["bel"]
                and bel["z"] == cell.get("z")
                and cell["cell_type"] in bel["compatible_cells"]
            ]
            if len(matching_bels) != 1:
                raise ValidationError(
                    f"{context}: illegal {cell['cell_type']} placement at "
                    f"{site['name']}/{cell['bel']} z={cell.get('z')!r}"
                )
            slot = (site["name"], cell["bel"])
            if slot in occupied:
                raise ValidationError(
                    f"{context}: BEL collision at {site['name']}/{cell['bel']}"
                )
            occupied.add(slot)
            if not isinstance(cell.get("fixed"), bool):
                raise ValidationError(f"{context}.fixed: expected a boolean")

        if expected_types is not None:
            missing = sorted(set(expected_types) - seen_instances)
            if missing:
                raise ValidationError(
                    "placement is incomplete; missing instances: "
                    + ", ".join(missing[:10])
                )

    def summary(self) -> Dict[str, Any]:
        return {
            "schema": PLACEMENT_SCHEMA,
            "part": self.value["part"],
            "cells": len(self.value["cells"]),
            "fixed_cells": sum(cell["fixed"] for cell in self.value["cells"]),
            "sites_used": len({cell["site"] for cell in self.value["cells"]}),
            "status": "legal",
        }

    def to_openparf_pl(self) -> str:
        lines = []
        for cell in sorted(self.value["cells"], key=lambda item: item["instance"]):
            suffix = " FIXED" if cell["fixed"] else ""
            lines.append(
                f"{cell['instance']} {cell['x']} {cell['y']} {cell['z']}{suffix}"
            )
        return "\n".join(lines) + "\n"

    def to_xdc(self) -> str:
        lines = [
            "# Generated by EmuFlow Phase 2 from a validated placement.",
            "# Each exact-name query must resolve to one cell in the mapped design.",
            "# FF LOC is fixed, but Vivado repairs the FF BEL within that SLICE",
            "# to satisfy UltraScale+ control-set sharing rules.",
        ]
        for index, cell in enumerate(
            sorted(self.value["cells"], key=lambda item: item["instance"])
        ):
            variable = f"emuflow_cell_{index}"
            expression = _vivado_regexp_literal(cell["instance"])
            lines.extend(
                [
                    f"# EmuIR instance: {cell['instance']}",
                    f"set {variable} [get_cells -hier -regexp "
                    f"{{^{expression}$}}]",
                    f"set_property LOC {cell['site']} ${variable}",
                ]
            )
            if not cell["cell_type"].startswith("FD"):
                lines.append(
                    f"set_property BEL {cell['bel']} ${variable}"
                )
        return "\n".join(lines) + "\n"

    def to_vivado_tsv(self) -> str:
        """Render a placement for O(1) exact-name lookup inside Vivado Tcl."""
        lines = ["# index\tvivado_name_utf8_hex\tsite\tbel\tcell_type"]
        for index, cell in enumerate(
            sorted(self.value["cells"], key=lambda item: item["instance"])
        ):
            name_hex = _vivado_mapped_name(cell["instance"]).encode(
                "utf-8"
            ).hex()
            lines.append(
                f"{index}\t{name_hex}\t{cell['site']}\t{cell['bel']}\t"
                f"{cell['cell_type']}"
            )
        return "\n".join(lines) + "\n"

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.value)
