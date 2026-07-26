# Phase 7C virtual-runtime and timing closure

## Result

Phase 7C closes the board-independent runtime boundary for the current
single-virtual-clock semantic envelope. The connected open-source PicoRV32
design now passes:

```text
real mapped DUT
  -> two legal register-output partitions
  -> system route and 32-slot TDM schedule
  -> per-FPGA transport plus integrated frame controller
  -> OpenPARF placement on both FPGAs
  -> two-clock Vivado route, DRC, and timing
  -> checked end-to-end QoR report
```

Validation date: 2026-07-27.

The routed DCPs were generated from source `74174e8`; the final
clock-group/QoR checker was source `42ae3d4`. The latter changes only
machine-readable timing extraction and validation, then reopens and checks the
same routed DCPs.

## Implemented runtime contract

Phase 7C adds:

- `emuflow.virtual-runtime/v1`, derived from BoardDB and the checked TDM
  schedule;
- a generated, synthesizable lockstep frame controller;
- an integrated controller instance in every per-FPGA transport module;
- `links_ready`, `virtual_clock_enable`, and `slot_debug` shell interfaces;
- a generated two-clock XDC contract;
- a 64-frame, two-controller self-checking SystemVerilog testbench;
- `emuflow.phase7b-physical-summary/v1`;
- `emuflow.qor-report/v1` and `emuflow.phase7c-report/v1`;
- an independent routed-DCP clock/route/DRC/timing reporter.

The controller holds slot 31 while global `links_ready` is false. When ready,
`virtual_clock_enable` is asserted during the release slot, before the
frame-boundary edge. A hardware shell must connect that signal to a dedicated
clock-buffer enable such as BUFGCE. Registering the enable after the boundary
would delay the DUT edge by one fabric cycle and make slot 0 transmit stale
state; the generated RTL and testbench explicitly reject that behavior.

All FPGA controllers require a common-frequency fabric clock, phase alignment
or trained deterministic slot alignment, synchronous reset release, and the
same synchronized global ready value. A fabric-routed combinational clock gate
is explicitly forbidden.

## Commands

Local regression:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Result: 60 tests passed.

Complete remote rebuild:

```bash
scripts/remote/proj169-2.sh picorv32-phase7c-all
```

Recheck existing routed DCPs and regenerate physical QoR:

```bash
scripts/remote/proj169-2.sh picorv32-phase7c-finalize
```

The full run regenerates Phase 6, remaps transport and controllers, reruns
OpenPARF on both partitions, generates the runtime contract, simulates it,
routes both designs with Vivado 2025.2, and independently reopens both DCPs.

## Logical runtime result

| Metric | Result |
| --- | ---: |
| fabric clock | 250 MHz / 4 ns |
| frame length | 32 slots / 128 ns |
| last scheduled arrival | slot 6 |
| barrier release | slot 31 |
| shadow-settle window | 25 slots / 100 ns |
| nominal virtual DUT rate | 7.8125 MHz |
| scheduled bit-hops | 140 |
| maximum TDM-domain utilization | 12.793% |
| schedule collisions | 0 |
| runtime simulation | 64 frames |
| injected barrier stall | 3 fabric cycles |
| lockstep controllers | 2 |

The rate is nominal: any `links_ready` stall lengthens a virtual DUT cycle.

The Phase 6 mapped equivalence gate remains unchanged and passes 64 virtual
DUT cycles, 102,208 FF-state comparisons, 12,864 top-output comparisons, and
zero mismatches.

## Placement and transport overhead

Adding the controller changes the real mapped transport totals from the
Phase 7A bootstrap result:

| FPGA | Original cells | Transport/controller cells | Merged cells | OpenPARF sites |
| --- | ---: | ---: | ---: | ---: |
| fpga0 | 3,463 | 284 | 3,747 | 446 |
| fpga1 | 349 | 127 | 476 | 73 |
| total | 3,812 | 411 | 4,223 | 519 |

The total transport/controller overhead is 10.78% relative to original DUT
cells. Both placements passed the independent Site/BEL legality checker.

## Routed timing result

Vivado routes with two explicit clocks:

- `emuflow_fabric_clk`: 4 ns;
- `emuflow_dut_clk`: 128 ns nominal.

The pausible-clock protocol also applies a 100 ns datapath-only maximum from
fabric shadow registers to DUT registers. This is the schedule-derived stable
data window, not a generic asynchronous CDC waiver.

| Metric | fpga0 | fpga1 |
| --- | ---: | ---: |
| routed cells | 3,747 | 476 |
| initial FF LOC repairs | 5 | 0 |
| unrouted nets | 0 | 0 |
| DRC violations | 0 | 0 |
| DUT-clock WNS | +121.157 ns | +121.337 ns |
| fabric-clock WNS | +2.768 ns | +2.642 ns |
| fabric-to-DUT window WNS | +98.017 ns | +98.457 ns |
| fabric-to-DUT endpoints | 109 | 67 |
| Vivado route wall time | 2:21.84 | 1:16.57 |
| peak RSS | 4,021,136 KiB | 3,905,300 KiB |

Both clock groups have zero failing endpoints. The clock-interaction report
identifies the fabric-to-DUT paths as constrained by `Max Delay Datapath
Only`; the independent reporter requires a real path set and non-negative
slack for DUT, fabric, and cross-domain groups.

Routed checkpoints:

| FPGA | Bytes | SHA-256 |
| --- | ---: | --- |
| fpga0 | 1,565,591 | `70119d00d65c5c74e8717e373b73439b392518b04939c8de5eef4d34c3356b61` |
| fpga1 | 551,856 | `11ca83581615d935d544906990444196a8420e346b61720d3c4142fcb793339a` |

## End-to-end QoR gate

The final checked report records:

| Boundary | Evidence |
| --- | --- |
| partition | 3,812 exact instances, 2 FPGAs, 140 cut nets |
| system route | 140 demands and bit-hops, 12.793% maximum utilization |
| TDM | completion at slot 6 of 32, zero collisions |
| runtime | 7.8125 MHz nominal, 100 ns shadow-settle window |
| equivalence | 64 cycles, zero mismatches |
| physical | 4,223 routed cells, zero unrouted nets, zero DRC violations |
| timing | worst overall WNS +2.642 ns |

The runtime contract, XDC, controller RTL, and 64-frame testbench are generated
twice and compared byte-for-byte. Their combined artifact-set SHA-256 is:

```text
46295d6a9fd9c32101e8b45f6ec9083cf8d73803792d4cda88adcdaa10194e1c
```

## Explicit boundary

This closes G0-G9 for the connected PicoRV32 under the virtual-board,
logic-only, single-virtual-clock envelope. It does not create a
hardware-ready bitstream.

The virtual BoardDB still deliberately omits package pins, banks,
IOSTANDARDs, source-synchronous input/output delays, physical clock-buffer
binding, link training, and a board shell. Those fields cannot be selected or
electrically checked without a real board BSP. Multi-clock DUTs and native
CARRY8/LUTRAM/BRAM/DSP packing are also later extensions, not claims of this
result.
