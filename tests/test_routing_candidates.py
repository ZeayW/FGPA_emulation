import copy
import tempfile
import unittest
from pathlib import Path

from emuflow.errors import ValidationError
from emuflow.platform import Platform
from emuflow.routing import demands_from_assignment, normalize_route_constraints
from emuflow.routing_candidates import validate_route_candidate_pool
from emuflow.routing_oracle import exact_route_tree_selection
from emuflow.timing_routing import (
    ROUTE_TDM_PROVIDER,
    compress_sta_paths,
    normalize_sta_paths,
    route_system_native,
)
from tests.native_build import tlr_router
from tests.test_phase4 import _assignment, _link, _platform_value


class RouteCandidatePoolTest(unittest.TestCase):
    def _fixture(self):
        platform = Platform.from_dict(
            _platform_value(
                "candidate_pool",
                ["a", "b", "c", "d", "e"],
                [
                    _link("ab", "a", "b", lanes=4),
                    _link("bd", "b", "d", lanes=4),
                    _link("be", "b", "e", lanes=4),
                    _link("ac", "a", "c", lanes=4),
                    _link("ce", "c", "e", lanes=4),
                ],
            )
        )
        assignment = _assignment(
            platform, [("multicast", "a", ["d", "e"])]
        )
        timing = compress_sta_paths(
            normalize_sta_paths(
                {
                    "schema": "emuflow.sta-paths/v1",
                    "design": "route_test",
                    "paths": [
                        {
                            "id": "multicast_path",
                            "clock_domain": "clk",
                            "clock_period_ns": 20.0,
                            "slack_ns": 10.0,
                            "fixed_delay_ns": 0.0,
                            "cut_nets": ["multicast"],
                        }
                    ],
                },
                demands_from_assignment(assignment, platform),
            )
        )
        constraints = normalize_route_constraints(
            {
                "schema": "emuflow.system-route-constraints/v1",
                "frame_slots": 8,
                "reroute_rounds": 0,
                "link_delay_ns": {
                    "ab": 1.0,
                    "bd": 1.0,
                    "be": 1.0,
                    "ac": 0.995,
                    "ce": 0.995,
                },
            },
            platform,
        )
        return platform, assignment, timing, constraints

    def test_pool_is_deterministic_and_contains_exact_small_tree(self) -> None:
        platform, assignment, timing, constraints = self._fixture()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = [root / "first.json", root / "second.json"]
            routes = []
            for path in paths:
                routes.append(
                    route_system_native(
                        assignment,
                        platform,
                        constraints,
                        timing,
                        executable=str(tlr_router()),
                        provider=ROUTE_TDM_PROVIDER,
                        candidate_pool_path=path,
                    )
                )
            self.assertEqual(paths[0].read_bytes(), paths[1].read_bytes())
            self.assertEqual(routes[0], routes[1])
            import json

            pool = json.loads(paths[0].read_text(encoding="utf-8"))
            checked = validate_route_candidate_pool(
                assignment, platform, pool
            )
            self.assertEqual(checked["status"], "pass")
            self.assertEqual(
                set(pool["metrics"]["candidates_by_generator"]),
                {
                    "shortest-path-tree",
                    "delay-demand-balanced",
                    "nearest-terminal-steiner",
                    "refined-final",
                },
            )
            oracle = exact_route_tree_selection(
                assignment, platform, constraints, timing
            )
            optimum = {tuple(edge) for edge in oracle["trees"]["multicast"]}
            generated = [
                {
                    (edge["link"], edge["from"], edge["to"])
                    for edge in candidate["tree_edges"]
                }
                for candidate in pool["candidates"]
            ]
            self.assertIn(optimum, generated)

    def test_pool_rejects_tampered_delay_and_tree(self) -> None:
        platform, assignment, timing, constraints = self._fixture()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "pool.json"
            route_system_native(
                assignment,
                platform,
                constraints,
                timing,
                executable=str(tlr_router()),
                provider=ROUTE_TDM_PROVIDER,
                candidate_pool_path=path,
            )
            import json

            pool = json.loads(path.read_text(encoding="utf-8"))
            broken_delay = copy.deepcopy(pool)
            broken_delay["candidates"][0]["predicted_max_delay_ns"] += 1.0
            with self.assertRaisesRegex(ValidationError, "delay"):
                validate_route_candidate_pool(
                    assignment, platform, broken_delay
                )
            broken_tree = copy.deepcopy(pool)
            broken_tree["candidates"][0]["tree_edges"].append(
                {"link": "ab", "from": "b", "to": "a"}
            )
            with self.assertRaisesRegex(
                ValidationError, "cycle|multiple path"
            ):
                validate_route_candidate_pool(
                    assignment, platform, broken_tree
                )


if __name__ == "__main__":
    unittest.main()
