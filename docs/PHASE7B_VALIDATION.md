# Phase 7B per-FPGA Vivado routing validation

## Scope

Phase 7B converts each merged placement EmuIR into a complete structural
Xilinx primitive Verilog netlist. The emitter preserves:

- all top-level scalar/vector ports;
- every LUT/FD primitive and stable instance identifier;
- LUT `INIT` and FF initialization parameters;
- constant primitive pin connections;
- Phase 7A source/shadow stitching and physical-link nets;
- KEEP and DONT_TOUCH attributes.

The existing Vivado validation harness then checks exact cell-name/count
agreement, applies every OpenPARF LOC/BEL, repairs only rejected FF control-set
locations, completes placement, routes, checks for unrouted nets, runs DRC and
timing reports, and writes routed checkpoints.

## Validation

Local regression:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Result: 55 tests passed.

Remote command:

```bash
scripts/remote/proj169-2.sh picorv32-phase7b
```

Validated source after the post-route summary correction:

```text
94b99f64b366eea43dd745ea893d60ab61018827
```

The correction only removed an invalid report-text assertion. Both routed
DCPs and both Tcl pass markers were already produced by source `b2812de`; the
Tcl gate directly queries nets with `ROUTE_STATUS == UNROUTED` and fails if
any exist.

## FPGA 0

| Metric | Value |
| --- | ---: |
| Structural cells | 3,734 |
| EmuIR nets | 3,808 |
| Top ports | 22 |
| OpenPARF FF LOC repairs | 5 |
| Fully routed nets | 3,650 |
| Nets with routing errors | 0 |
| DRC checks found | 0 |
| DUT-clock WNS at 10 ns | +3.834 ns |
| Wall time | 2:23.47 |
| Peak RSS | 4,035,524 KiB |

Routed DCP:

```text
size=1,554,513 bytes
SHA-256=c762c92c0761c69ebc24f0491ff529989143a023a11f3751e31d7e1b83ef4ad6
```

## FPGA 1

| Metric | Value |
| --- | ---: |
| Structural cells | 463 |
| EmuIR nets | 504 |
| Top ports | 8 |
| OpenPARF FF LOC repairs | 0 |
| Fully routed nets | 430 |
| Nets with routing errors | 0 |
| DRC checks found | 0 |
| DUT-clock WNS at 10 ns | +4.439 ns |
| Wall time | 1:16.55 |
| Peak RSS | 3,903,828 KiB |

Routed DCP:

```text
size=539,242 bytes
SHA-256=c41586a8181c668b829353e17ffaa31bc64131f9f6ad1f5a545148776c69f2e7
```

Together the routed designs retain all 4,197 Phase 7A cells, including 385
mapped transport cells.

## Explicit boundary

Vivado runs in out-of-context mode. The 10 ns constraint covers the virtual
DUT `clk`; `fabric_clk`, board-link input/output delay, clock-domain crossing,
package pins, and source-synchronous timing are not yet constrained. The
positive WNS values therefore demonstrate DUT-clock timing only.

The virtual BoardDB still has no real package-pin binding. These routed DCPs
are a per-FPGA fabric closure result, not hardware-ready bitstreams. Phase 7C
must add a hardware BSP, electrical pin checks, board timing, and bitstream
generation when a board is selected.

Phase 7C has since added the board-independent single-virtual-DUT/fabric
pausible-clock contract, integrated barrier controllers, separate timing
groups, and DCP QoR aggregation. See `docs/PHASE7C_VALIDATION.md`. Package
pins and physical link IO timing still require a hardware BSP.
