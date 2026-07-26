# Phase 5 TDM scheduling and transport validation

## Result

The Phase 5 scheduling and transport increment is implemented and validated
on `proj169-2` using all 140 routed bit-hop demands from connected PicoRV32:

```text
Phase 4 route trees
  -> latency-aware store-and-forward precedence
  -> directed/half-duplex collision domains
  -> concrete slot and lane assignment
  -> independent schedule reconstruction
  -> schedule ROM/endpoint metadata
  -> Python multi-frame transport simulation
  -> generated SystemVerilog transport simulation
```

Validation date: 2026-07-27.

All 140 routed bit-hops receive a legal lane/slot, all 140 remote sinks
complete within the 32-slot virtual frame, and there are zero collisions. The
schedule finishes at slot 6. Sixty-four simulated frames deliver 8,960 sink
values without mismatch in both the independent Python model and a compiled
SystemVerilog transport test.

Full partitioned-PicoRV32 versus unpartitioned-PicoRV32 cycle equivalence
requires Phase 6 logical netlist splitting and endpoint insertion. It remains
a joint G6/Phase 6 hard gate and is not claimed by this schedule-level result.

## Implemented artifacts and commands

Phase 5 adds:

- `emuflow.tdm-schedule/v1`: every routed bit-hop mapped to a link capacity
  domain, slot, lane, ready time, and arrival time;
- `emuflow.transport-manifest/v1`: stable demand indices and per-FPGA TX/RX
  schedule entries;
- `schedule.tsv`: an inspectable schedule-ROM source table;
- generated `transport_schedule_tb.sv`: schedule-specific self-checking
  transport simulation;
- synthesizable `emuflow_tdm_link.sv` and `emuflow_frame_barrier.sv`
  primitives;
- `emuflow.phase5-report/v1`: checker and transport-simulation results.

CLI:

```bash
PYTHONPATH=src python3 -m emuflow phase5 \
  --routes build/phase4/routes.json \
  --platform platforms/virtual/xcvu3p_2fpga_p2p.json \
  --out build/phase5 \
  --simulation-frames 64

PYTHONPATH=src python3 -m emuflow schedule validate \
  build/phase5/schedule.json \
  --routes build/phase4/routes.json \
  --platform platforms/virtual/xcvu3p_2fpga_p2p.json
```

Remote regression:

```bash
scripts/remote/proj169-2.sh picorv32-phase5
```

## Scheduling semantics

Each entry reserves one `(capacity domain, slot, lane)` tuple. Full-duplex
directions are independent domains; half-duplex directions share one domain.
An entry launched in slot `s` over a link with latency `l` arrives in
`s + l`.

For multi-hop trees, a child hop may launch only after its parent arrival plus
one store-and-forward cycle. The final arrival must be strictly inside the
frame so the barrier can assert the virtual clock-enable after every cut value
is stable.

The deterministic earliest-slot provider walks each multicast tree in
topological order, reserves the first available legal lane, and fails with the
specific demand/edge/ready-time if no slot exists.

## Independent checker

The checker reconstructs every required hop from `routes.json` and verifies:

- exact one-to-one hop coverage;
- directed link and shared half-duplex capacity domain;
- slot and lane bounds;
- no lane/slot collision;
- link-latency arrival time;
- multi-hop store-and-forward precedence;
- every sink completion before the frame boundary;
- per-domain utilization and summary metrics.

Tests deliberately corrupt lane/slot and precedence fields. Both corruptions
are rejected. Another test demonstrates that a route can fit the Phase 4
aggregate capacity while becoming infeasible after Phase 5 reserves link
latency at the end of the frame.

## Connected PicoRV32 result

| Metric | Result |
| --- | ---: |
| routed demands | 140 |
| scheduled bit-hops | 140 |
| remote sinks completed | 140 |
| frame slots | 32 |
| final completion slot | 6 |
| collisions | 0 |
| maximum domain utilization | 12.793% |
| schedule runtime | 0.12 s |
| peak RSS | 18,384 KiB |
| schedule JSON | 58,165 bytes |
| schedule SHA-256 | `93d43eca8eb497e1d489de95a470bc4be17739c93778fb10e0fec7df2a293691` |

Directional schedules:

| Direction | Bit-hops | Slot/lane capacity | Utilization |
| --- | ---: | ---: | ---: |
| `fpga0 -> fpga1` | 9 | 1,024 | 0.879% |
| `fpga1 -> fpga0` | 131 | 1,024 | 12.793% |

Launch-slot occupancy is 41, 32, 32, 32, and 3 entries in slots 0 through 4.
All values arrive by slot 6 after the two-cycle link latency.

## Transport validation

The Python event model runs 64 frames with a deterministic independent bit for
every demand. It models launch, link arrival, intermediate FPGA state, and
every sink comparison:

| Metric | Result |
| --- | ---: |
| frames | 64 |
| delivered sink values | 8,960 |
| mismatches | 0 |
| trace SHA-256 | `8a98c717be3be9988b7c469056b81f13d78ca593f565370e66ef8c4804c85487` |

The generated SystemVerilog testbench instantiates the generic TDM link RTL,
drives real schedule lanes on every slot, captures values after modeled
latency, and checks all demand sinks on every frame. Icarus Verilog compiled
and ran it with:

```text
EMUFLOW_TDM_RTL_SIM status=pass frames=64 demands=140 entries=140
```

The generic frame barrier separately compiles as SystemVerilog. The generated
transport executable is 198,973 bytes.

The complete schedule was generated twice. The two `schedule.json` files are
byte-identical and have the same SHA-256.

## Additional coverage

The suite now has 49 passing tests, including:

- multi-hop store-and-forward precedence;
- half-duplex opposing-direction scheduling;
- latency-induced infeasibility;
- collision and precedence artifact corruption;
- schedule-specific RTL generation;
- multi-frame delivery simulation;
- the complete local route-to-schedule pipeline.

## G6 status and Phase 6 plan

The schedule/transport portion of G6 passes:

- no lane/slot collision;
- all multi-hop precedence checks hold;
- every frame completes before virtual clock-enable;
- schedule-level Python and RTL transport results agree.

The remaining G6 condition is full DUT cycle equivalence. Phase 6 will add:

1. logical netlist splitting by Phase 3 instance assignment;
2. cut-net TX/RX shadow endpoint insertion;
3. per-FPGA schedule ROM and mux/demux generation;
4. logical lane-map agreement checks;
5. virtual I/O-region constraints;
6. a two-partition PicoRV32 simulation harness;
7. cycle-by-cycle comparison with the original mapped design;
8. independent per-FPGA EmuIR coverage and endpoint-connectivity checks.
