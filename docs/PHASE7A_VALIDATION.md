# Phase 7A per-FPGA OpenPARF placement validation

## Scope

Phase 7A is the first physical-backend increment after multi-FPGA splitting.
For each FPGA it:

1. synthesizes the generated schedule-specific transport RTL with real Yosys;
2. imports the mapped transport LUT/FF graph into EmuIR;
3. stitches `source_values` to original local source nets;
4. stitches transport `shadow_values` drivers to original remote sinks;
5. namespaces and merges every remaining mapped transport primitive/net;
6. exports the merged graph to OpenPARF Bookshelf;
7. runs OpenPARF global placement and UltraScale slot legalization;
8. independently reloads and checks every Site/BEL assignment.

This avoids estimating transport overhead. Placement includes the primitives
actually emitted by `synth_xilinx -family xcup` under the logic-only policy.

## Local regression

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Result: 54 tests passed. The new lowering test verifies that a mapped
transport shadow-register output becomes the actual driver of the original
remote sink net and that the synthetic `shadow_values` boundary is removed.

## Real RTL experiment

Command:

```bash
scripts/remote/proj169-2.sh picorv32-phase7a
```

Validated source:

```text
05f200d91d143620e5f75db6dba301d8f97a3371
```

Input is the Phase 6 connected-PicoRV32 split. Both partitions use the real
`xcvu3p-ffvc1517-2-e` ArchitectureDB exported by Vivado and the installed
CPU-only OpenPARF build on `proj169-2`.

### FPGA 0

| Metric | Value |
| --- | ---: |
| Original partition cells | 3,463 |
| Synthesized transport cells | 271 |
| Merged placement cells | 3,734 |
| Merged LUTs | 2,137 |
| Merged FFs | 1,597 |
| Merged nets | 3,808 |
| Legal sites used | 445 |

Transport types:

```text
FDRE=131, LUT5=131, LUT6=9
```

### FPGA 1

| Metric | Value |
| --- | ---: |
| Original partition cells | 349 |
| Synthesized transport cells | 114 |
| Merged placement cells | 463 |
| Merged LUTs | 323 |
| Merged FFs | 140 |
| Merged nets | 504 |
| Legal sites used | 74 |

Transport types:

```text
FDRE=9, LUT5=3, LUT6=102
```

Across both FPGAs, all 3,812 original cells remain covered and the real mapped
transport adds 385 cells, producing 4,197 independently placed cells.

## Legality and reproducibility

The existing placement checker validates completeness, cell/BEL
compatibility, one-cell/one-BEL ownership, and collisions. Both placements
report `status=legal` and the checked placement cell count exactly matches the
merged EmuIR count.

The full command was run twice. Fixed-seed OpenPARF output was byte-identical:

| FPGA | Placement JSON SHA-256 |
| --- | --- |
| FPGA 0 | `4b342b092a527014635f4ae8844baf0e048278760010572595014e26bc594ca8` |
| FPGA 1 | `8179df659f7542eaffcdf54d14c6198b9c9e9f39c10235402ea4d6f6fc398d39` |

First measured run:

| Metric | Value |
| --- | ---: |
| Wall time | 27.92 s |
| User time | 66.74 s |
| System time | 17.38 s |
| Peak RSS | 1,115,452 KiB |

User time exceeds wall time because OpenPARF uses eight CPU threads.

## Explicit boundary

Bookshelf placement ignores nets with fewer than two cell endpoints. Physical
link output nets ending only at a top-level port therefore do not influence
placement, although all mapped transport LUTs remain present and legal. The
next increment must preserve these IO nets while producing a complete
per-FPGA physical netlist, bind virtual IO regions, and run routing.

Phase 7A does not claim Vivado route, DRC, timing, DCP, or bitstream closure.
Those are the Phase 7B/7C gates.
