# Phase 6 board-independent split and lane-planning validation

## Scope

This increment turns the Phase 3 assignment and Phase 5 schedule into explicit
per-FPGA logical artifacts:

- one logical netlist per FPGA with exact original-instance ownership;
- TX/RX transport endpoints for every scheduled board-link hop;
- cut-net shadow drivers on receiving partitions;
- one logical lane-map record agreeing with both endpoint identifiers;
- per-FPGA schedule-specific transport mux/capture RTL;
- virtual IO-region anchors and a deliberately non-binding XDC template;
- an independent artifact reconstruction checker;
- a mapped LUT/FF cycle-equivalence model.

Package pins, banks, IOSTANDARDs, source-synchronous clock pins, and GT-quad
bindings are not invented for the virtual BoardDB. They remain a hardware-BSP
dependent Phase 6 binding increment. Generated `.xdc.template` files contain
only commented placeholders and must not be sourced by Vivado.

## Artifact contracts

Phase 6 introduces:

- `emuflow.fpga-netlist/v1`;
- `emuflow.transport-endpoints/v1`;
- `emuflow.logical-lane-map/v1`;
- `emuflow.virtual-io-anchors/v1`;
- `emuflow.split-manifest/v1`;
- `emuflow.phase6-report/v1`.

The Yosys importer now retains constant primitive pin connections. TDM
schedules retain their route source, sink, and tree-edge metadata, so the
splitter does not have to infer transport direction from lane entries.

For each scheduled hop, the splitter creates exactly one TX and one RX
endpoint. A TX reads either the original source net or a previously received
shadow value on a transit FPGA. The corresponding RX writes a demand-specific
shadow register. Original sinks on a remote FPGA are driven by that shadow;
multiple sinks may share it without duplicating a board transfer.

## Local regression

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Result: 53 tests passed. Phase 6 tests cover exact cell coverage, lane-map
agreement, corruption rejection, constant-pin retention, artifact reload, and
12-cycle mapped-counter equivalence.

## Real RTL experiment on `proj169-2`

```bash
scripts/remote/proj169-2.sh picorv32-phase6
```

Validated repository source:

```text
64dff5e8f35899d85789d7c4b091e9671577bbe4
```

Input is the existing real Yosys `synth_xilinx -family xcup` result for the
open-source PicoRV32 `picorv32` top. Phase 6 re-imports that mapped JSON to
retain 1,779 constant primitive-pin connections, then rebuilds the Phase 5
schedule with explicit route metadata.

Mapped design:

| Metric | Value |
| --- | ---: |
| Total primitives | 3,812 |
| LUT1–LUT6 | 2,215 |
| FDRE/FDSE | 1,597 |
| Cross-FPGA bit demands | 140 |
| Remote original sink endpoints | 245 |

Split result:

| Metric | FPGA 0 | FPGA 1 | Total |
| --- | ---: | ---: | ---: |
| Original primitives | 3,463 | 349 | 3,812 |
| Local net segments | 3,629 | 360 | 3,989 |
| Original source signals exported | 9 | 131 | 140 |
| Received shadow signals | 131 | 9 | 140 |
| Virtual lane anchors | 41 | 41 | 82 |

Global Phase 6 checks:

| Check | Result |
| --- | ---: |
| Scheduled hops | 140 |
| Paired TX/RX endpoints | 280 |
| Logical lane-map entries | 140 |
| Instance coverage errors | 0 |
| Endpoint agreement errors | 0 |
| Unbound virtual anchors | 82 |

The anchor count is lower than the endpoint count because the same physical
logical lane is reused in different TDM slots.

## Cycle-equivalence gate

The checker models the actual mapped primitive set used by this design:
LUT1–LUT6 `INIT` truth tables and FDRE/FDSE `D`, enable, reset/set, and initial
state semantics. It drives deterministic top-level input vectors, evaluates
the unpartitioned mapped graph, transports every cut value according to the
scheduled hop/slot/latency order, evaluates the split graph through its shadow
inputs, and compares the next FF state plus every top-level output bit.

| Metric | Value |
| --- | ---: |
| Virtual DUT cycles | 64 |
| Compared FF state bits | 102,208 |
| Compared top-output bits | 12,864 |
| Mismatches | 0 |
| Trace SHA-256 | `758cbf7ab866138ce8ab48d5f4ea6fb90f649c3d76b8e6e719fb4d76bf421737` |

This closes the joint Phase 5/6 logical cycle-equivalence gate for the mapped
PicoRV32 primitive envelope. It is not a claim that arbitrary unsupported
vendor macros or multi-clock designs are equivalent; the checker rejects
unsupported primitive types instead of silently approximating them.

Both generated per-FPGA transport modules compile with `/usr/bin/iverilog`
using SystemVerilog 2012. Both compile logs are empty. The complete artifact
set was generated twice and compared by content:

```text
artifact-set SHA-256:
8ade177eb1a1c32a2ca7afac0212998dbd6857eb6e5f1117bfc572f77223855c
```

Measured Phase 6 run:

| Metric | Value |
| --- | ---: |
| Wall time | 4.26 s |
| User time | 4.21 s |
| System time | 0.04 s |
| Peak RSS | 57,256 KiB |
| Primary artifact directory | 6.7 MiB |

## Next increment

The next board-independent stage will lower each per-FPGA logical artifact
into a placement-ready primitive graph, include generated transport resources
in capacity accounting, and run OpenPARF independently for both partitions.
It will preserve virtual IO-region anchors. Hardware package-pin/XDC closure
will remain a separate BSP binding gate until a board is selected.
