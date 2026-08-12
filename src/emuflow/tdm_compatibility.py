"""Clock/protocol compatibility domains for TDM lane grouping."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Mapping, Set

from .errors import ValidationError


TDM_COMPATIBILITY_PROVIDER = "clock-protocol-compatibility-v1"


def derive_tdm_compatibility(model: Mapping[str, Any]) -> Dict[str, Any]:
    """Derive one deterministic compatibility class for every routed hop."""

    clocks_by_hop: Dict[int, Set[str]] = defaultdict(set)
    for path in model["timing_paths"]:
        for hop in path["hops"]:
            clocks_by_hop[hop].add(path["clock_domain"])
    protocol = "static-tdm-frame"
    identity_by_hop = {
        hop["index"]: (
            protocol,
            hop.get("compatibility_domain", "global-frame-cdc"),
        )
        for hop in model["hops"]
    }
    identities = sorted(set(identity_by_hop.values()))
    class_index = {
        identity: index for index, identity in enumerate(identities)
    }
    class_clocks: Dict[int, Set[str]] = defaultdict(set)
    for hop in model["hops"]:
        class_clocks[class_index[identity_by_hop[hop["index"]]]].update(
            clocks_by_hop[hop["index"]]
        )
    classes = [
        {
            "index": class_index[identity],
            "protocol": identity[0],
            "transport_domain": identity[1],
            "clock_domains": sorted(class_clocks[class_index[identity]])
            or ["unconstrained"],
        }
        for identity in identities
    ]
    hops = []
    for hop in model["hops"]:
        identity = identity_by_hop[hop["index"]]
        hops.append(
            {
                "hop": hop["index"],
                "compatibility": class_index[identity],
                "clock_domains": sorted(clocks_by_hop[hop["index"]])
                or ["unconstrained"],
            }
        )
    return {
        "provider": TDM_COMPATIBILITY_PROVIDER,
        "classes": classes,
        "hops": hops,
    }


def validate_tdm_compatibility(
    model: Mapping[str, Any], compatibility: Mapping[str, Any]
) -> Dict[str, Any]:
    expected = derive_tdm_compatibility(model)
    if compatibility != expected:
        raise ValidationError(
            "TDM clock/protocol compatibility does not match routes"
        )
    return {
        "status": "pass",
        "classes": len(expected["classes"]),
        "hops": len(expected["hops"]),
    }
