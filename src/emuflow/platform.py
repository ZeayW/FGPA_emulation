import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .errors import ValidationError
from .io import read_json
from .resources import RESOURCE_FIELDS


BOARDDB_SCHEMA = "emuflow.boarddb/v1"
VALID_PLATFORM_KINDS = {"virtual", "hardware"}
VALID_DIRECTIONS = {"full_duplex", "half_duplex", "unidirectional"}
VALID_LINK_MODES = {"source_synchronous", "parallel", "serial", "abstract"}
VALID_CAPACITY_SHARING = {"per_direction", "shared_bidirectional"}


def _require_mapping(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{context}: expected an object")
    return value


def _require_nonempty_string(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{context}: expected a non-empty string")
    return value


@dataclass(frozen=True)
class FpgaNode:
    id: str
    part: str
    utilization_limit: float
    capacity: Dict[str, int]

    @property
    def effective_capacity(self) -> Dict[str, int]:
        return {
            resource: math.floor(count * self.utilization_limit)
            for resource, count in self.capacity.items()
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "part": self.part,
            "utilization_limit": self.utilization_limit,
            "capacity": dict(sorted(self.capacity.items())),
            "effective_capacity": dict(sorted(self.effective_capacity.items())),
        }


@dataclass(frozen=True)
class BoardLink:
    id: str
    endpoints: Tuple[str, str]
    direction: str
    mode: str
    data_lanes_per_direction: int
    fabric_clock_mhz: float
    latency_cycles: int
    capacity_sharing: str = "per_direction"
    payload_bits_per_lane_per_cycle: int = 1
    max_line_rate_gbps_per_lane: Optional[float] = None

    @property
    def transport_bits_per_cycle_per_direction(self) -> int:
        """User-side bit capacity exposed to routing and TDM.

        Parallel links carry one bit per physical lane and cycle.  A serial
        transceiver presents a wider user-side word for every physical lane;
        the serializer itself remains a board-support-package concern.
        """
        return (
            self.data_lanes_per_direction
            * self.payload_bits_per_lane_per_cycle
        )

    @property
    def raw_bits_per_second_per_direction(self) -> float:
        return (
            self.transport_bits_per_cycle_per_direction
            * self.fabric_clock_mhz
            * 1_000_000.0
        )

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "id": self.id,
            "endpoints": list(self.endpoints),
            "direction": self.direction,
            "mode": self.mode,
            "data_lanes_per_direction": self.data_lanes_per_direction,
            "fabric_clock_mhz": self.fabric_clock_mhz,
            "latency_cycles": self.latency_cycles,
            "capacity_sharing": self.capacity_sharing,
            "raw_bits_per_second_per_direction": (
                self.raw_bits_per_second_per_direction
            ),
        }
        if self.payload_bits_per_lane_per_cycle != 1:
            result["payload_bits_per_lane_per_cycle"] = (
                self.payload_bits_per_lane_per_cycle
            )
            result["transport_bits_per_cycle_per_direction"] = (
                self.transport_bits_per_cycle_per_direction
            )
        if self.max_line_rate_gbps_per_lane is not None:
            result["max_line_rate_gbps_per_lane"] = (
                self.max_line_rate_gbps_per_lane
            )
        return result


@dataclass(frozen=True)
class Platform:
    name: str
    kind: str
    description: str
    fpgas: Tuple[FpgaNode, ...]
    links: Tuple[BoardLink, ...]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Platform":
        if value.get("schema") != BOARDDB_SCHEMA:
            raise ValidationError(
                f"platform.schema: expected {BOARDDB_SCHEMA!r}, "
                f"got {value.get('schema')!r}"
            )

        metadata = _require_mapping(value.get("platform"), "platform.platform")
        name = _require_nonempty_string(metadata.get("name"), "platform.name")
        kind = _require_nonempty_string(metadata.get("kind"), "platform.kind")
        if kind not in VALID_PLATFORM_KINDS:
            raise ValidationError(
                f"platform.kind: expected one of {sorted(VALID_PLATFORM_KINDS)}"
            )
        description = metadata.get("description", "")
        if not isinstance(description, str):
            raise ValidationError("platform.description: expected a string")

        raw_fpgas = value.get("fpgas")
        if not isinstance(raw_fpgas, list) or not raw_fpgas:
            raise ValidationError("platform.fpgas: expected a non-empty array")

        fpgas: List[FpgaNode] = []
        fpga_ids = set()
        for index, raw in enumerate(raw_fpgas):
            item = _require_mapping(raw, f"fpgas[{index}]")
            fpga_id = _require_nonempty_string(item.get("id"), f"fpgas[{index}].id")
            if fpga_id in fpga_ids:
                raise ValidationError(f"fpgas[{index}].id: duplicate {fpga_id!r}")
            fpga_ids.add(fpga_id)
            part = _require_nonempty_string(
                item.get("part"), f"fpgas[{index}].part"
            )
            utilization_limit = item.get("utilization_limit")
            if (
                isinstance(utilization_limit, bool)
                or not isinstance(utilization_limit, (int, float))
                or not 0.0 < float(utilization_limit) <= 1.0
            ):
                raise ValidationError(
                    f"fpgas[{index}].utilization_limit: expected 0 < value <= 1"
                )
            raw_capacity = _require_mapping(
                item.get("capacity"), f"fpgas[{index}].capacity"
            )
            capacity: Dict[str, int] = {}
            for resource, raw_count in raw_capacity.items():
                if resource not in RESOURCE_FIELDS:
                    raise ValidationError(
                        f"fpgas[{index}].capacity: unknown resource {resource!r}"
                    )
                if (
                    isinstance(raw_count, bool)
                    or not isinstance(raw_count, int)
                    or raw_count < 0
                ):
                    raise ValidationError(
                        f"fpgas[{index}].capacity.{resource}: "
                        "expected a non-negative integer"
                    )
                capacity[resource] = raw_count
            if not capacity:
                raise ValidationError(
                    f"fpgas[{index}].capacity: expected at least one resource"
                )
            fpgas.append(
                FpgaNode(
                    id=fpga_id,
                    part=part,
                    utilization_limit=float(utilization_limit),
                    capacity=capacity,
                )
            )

        raw_links = value.get("links")
        if not isinstance(raw_links, list):
            raise ValidationError("platform.links: expected an array")
        links: List[BoardLink] = []
        link_ids = set()
        for index, raw in enumerate(raw_links):
            item = _require_mapping(raw, f"links[{index}]")
            link_id = _require_nonempty_string(item.get("id"), f"links[{index}].id")
            if link_id in link_ids:
                raise ValidationError(f"links[{index}].id: duplicate {link_id!r}")
            link_ids.add(link_id)

            endpoints = item.get("endpoints")
            if (
                not isinstance(endpoints, list)
                or len(endpoints) != 2
                or not all(isinstance(endpoint, str) for endpoint in endpoints)
            ):
                raise ValidationError(
                    f"links[{index}].endpoints: expected two FPGA IDs"
                )
            if endpoints[0] == endpoints[1]:
                raise ValidationError(
                    f"links[{index}].endpoints: self-links are not allowed"
                )
            missing = [endpoint for endpoint in endpoints if endpoint not in fpga_ids]
            if missing:
                raise ValidationError(
                    f"links[{index}].endpoints: unknown FPGA IDs {missing}"
                )

            direction = _require_nonempty_string(
                item.get("direction"), f"links[{index}].direction"
            )
            if direction not in VALID_DIRECTIONS:
                raise ValidationError(
                    f"links[{index}].direction: expected one of "
                    f"{sorted(VALID_DIRECTIONS)}"
                )
            mode = _require_nonempty_string(
                item.get("mode"), f"links[{index}].mode"
            )
            if mode not in VALID_LINK_MODES:
                raise ValidationError(
                    f"links[{index}].mode: expected one of "
                    f"{sorted(VALID_LINK_MODES)}"
                )
            capacity_sharing = item.get(
                "capacity_sharing", "per_direction"
            )
            if capacity_sharing not in VALID_CAPACITY_SHARING:
                raise ValidationError(
                    f"links[{index}].capacity_sharing: expected one of "
                    f"{sorted(VALID_CAPACITY_SHARING)}"
                )
            if (
                capacity_sharing == "shared_bidirectional"
                and direction != "full_duplex"
            ):
                raise ValidationError(
                    f"links[{index}].capacity_sharing: "
                    "shared_bidirectional requires full_duplex direction"
                )

            lanes = item.get("data_lanes_per_direction")
            if isinstance(lanes, bool) or not isinstance(lanes, int) or lanes <= 0:
                raise ValidationError(
                    f"links[{index}].data_lanes_per_direction: "
                    "expected a positive integer"
                )
            frequency = item.get("fabric_clock_mhz")
            if (
                isinstance(frequency, bool)
                or not isinstance(frequency, (int, float))
                or frequency <= 0
            ):
                raise ValidationError(
                    f"links[{index}].fabric_clock_mhz: expected a positive number"
                )
            latency = item.get("latency_cycles")
            if (
                isinstance(latency, bool)
                or not isinstance(latency, int)
                or latency < 0
            ):
                raise ValidationError(
                    f"links[{index}].latency_cycles: "
                    "expected a non-negative integer"
                )
            payload_width = item.get("payload_bits_per_lane_per_cycle", 1)
            if (
                isinstance(payload_width, bool)
                or not isinstance(payload_width, int)
                or payload_width <= 0
            ):
                raise ValidationError(
                    f"links[{index}].payload_bits_per_lane_per_cycle: "
                    "expected a positive integer"
                )
            if mode != "serial" and payload_width != 1:
                raise ValidationError(
                    f"links[{index}].payload_bits_per_lane_per_cycle: "
                    "values greater than one require serial mode"
                )
            line_rate = item.get("max_line_rate_gbps_per_lane")
            if line_rate is not None:
                if (
                    mode != "serial"
                    or isinstance(line_rate, bool)
                    or not isinstance(line_rate, (int, float))
                    or float(line_rate) <= 0.0
                ):
                    raise ValidationError(
                        f"links[{index}].max_line_rate_gbps_per_lane: "
                        "expected a positive number for a serial link"
                    )
                configured_rate = float(frequency) * payload_width / 1000.0
                if configured_rate > float(line_rate) * (1.0 + 1e-9):
                    raise ValidationError(
                        f"links[{index}]: configured user-side rate "
                        f"{configured_rate:g} Gbps/lane exceeds maximum "
                        f"line rate {float(line_rate):g} Gbps/lane"
                    )
            links.append(
                BoardLink(
                    id=link_id,
                    endpoints=(endpoints[0], endpoints[1]),
                    direction=direction,
                    mode=mode,
                    data_lanes_per_direction=lanes,
                    fabric_clock_mhz=float(frequency),
                    latency_cycles=latency,
                    capacity_sharing=capacity_sharing,
                    payload_bits_per_lane_per_cycle=payload_width,
                    max_line_rate_gbps_per_lane=(
                        None if line_rate is None else float(line_rate)
                    ),
                )
            )

        return cls(
            name=name,
            kind=kind,
            description=description,
            fpgas=tuple(fpgas),
            links=tuple(links),
        )

    @classmethod
    def load(cls, path: Path) -> "Platform":
        return cls.from_dict(read_json(path))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": BOARDDB_SCHEMA,
            "platform": {
                "name": self.name,
                "kind": self.kind,
                "description": self.description,
            },
            "fpgas": [fpga.to_dict() for fpga in self.fpgas],
            "links": [link.to_dict() for link in self.links],
        }

    def summary(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "fpga_count": len(self.fpgas),
            "link_count": len(self.links),
            "parts": sorted({fpga.part for fpga in self.fpgas}),
            "total_raw_link_gbps_per_direction": sum(
                link.raw_bits_per_second_per_direction for link in self.links
            )
            / 1_000_000_000.0,
        }
