import copy
import unittest

from emuflow.errors import ValidationError
from emuflow.physical_backend import (
    PHYSICAL_PARTITION_RESULT_SCHEMA,
    physical_backend_descriptor,
    physical_summary_item,
    validate_physical_backend_descriptor,
    validate_physical_partition_result,
)


class PhysicalBackendContractTest(unittest.TestCase):
    def _result(self):
        return {
            "schema": PHYSICAL_PARTITION_RESULT_SCHEMA,
            "status": "pass",
            "identity": {
                "backend": "open",
                "fpga": "fpga0",
                "part": "academic-part",
            },
            "cell_accounting": {
                "original_cells": 100,
                "transport_cells": 5,
                "routed_cells": 105,
                "physical_cells": 106,
                "infrastructure_cells": 1,
                "optimization_cells": 0,
            },
            "closure": {"unrouted_nets": 0, "drc_violations": 0},
            "clocks": {"fabric_period_ns": 4.0, "dut_period_ns": 100.0},
            "timing": {
                "wns_ns": 0.5,
                "dut_wns_ns": 90.0,
                "fabric_wns_ns": 0.5,
                "fabric_to_dut_wns_ns": 2.0,
                "critical_path_ns": 10.0,
            },
            "artifacts": {"route": {"sha256": "0" * 64}},
        }

    def test_descriptors_expose_the_same_capability_interface(self):
        open_backend = physical_backend_descriptor("open")
        vivado_backend = physical_backend_descriptor("vivado")
        self.assertEqual(
            set(open_backend["capabilities"]),
            set(vivado_backend["capabilities"]),
        )
        self.assertEqual(
            validate_physical_backend_descriptor(vivado_backend)["backend"],
            "vivado",
        )

    def test_common_result_projects_to_phase7b(self):
        result = self._result()
        result["cell_accounting"].update(
            {"physical_cells": 109, "optimization_cells": 3}
        )
        validation = validate_physical_partition_result(
            result,
            backend="open",
            fpga="fpga0",
            part="academic-part",
            original_cells=100,
            transport_cells=5,
        )
        summary = physical_summary_item(result)
        self.assertEqual(validation["routed_cells"], 105)
        self.assertEqual(summary["physical_cells"], 109)
        self.assertEqual(summary["timing"]["fabric_wns_ns"], 0.5)

    def test_common_result_rejects_provider_identity_and_timing_failure(self):
        result = self._result()
        result["identity"]["backend"] = "vivado"
        with self.assertRaisesRegex(ValidationError, "identity"):
            validate_physical_partition_result(
                result,
                backend="open",
                fpga="fpga0",
                part="academic-part",
                original_cells=100,
                transport_cells=5,
            )
        result = copy.deepcopy(self._result())
        result["timing"]["wns_ns"] = -0.01
        with self.assertRaisesRegex(ValidationError, "did not meet timing"):
            validate_physical_partition_result(
                result,
                backend="open",
                fpga="fpga0",
                part="academic-part",
                original_cells=100,
                transport_cells=5,
            )


if __name__ == "__main__":
    unittest.main()
