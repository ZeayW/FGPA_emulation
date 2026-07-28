# Phase 7D board-independent release audit

## Result

Phase 7D seals the connected PicoRV32 board-independent run as one
cross-checked G0-G9 release rather than a collection of independently passing
phase directories.

Validation date: 2026-07-27.

Validated source:

```text
b71890de3b2c5bae02420c09762a321c4152f322
```

## Implemented release boundary

The new `emuflow.release-manifest/v1` auditor reloads and cross-checks:

- the benchmark source inventory and source-file SHA-256 values;
- the Phase 1 benchmark gates;
- Phase 3 partition coverage and cut legality;
- Phase 4 routed-demand and overload results;
- Phase 5 schedule occupancy and collision results;
- Phase 6 split coverage, lane agreement, and mapped cycle equivalence;
- both Phase 7A lowering and real OpenPARF placement reports;
- both Phase 7B mapped-Verilog reports and routed DCP records;
- the Phase 7C runtime, physical summary, and QoR reports;
- a caller-selected inventory of release-critical artifacts.

It rejects any disagreement in design/platform identity, original cell count,
cut/route/schedule/split counts, merged/placed/emitted/routed cells, transport
overhead, FPGA coverage, runtime frame/completion slots, source hashes,
artifact existence, route/DRC state, or the three timing groups.

The manifest contains hashes and byte sizes, not machine-specific absolute
artifact paths, so repeated audits of the same run are byte-reproducible.

## Validation

Local regression:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Result: 63 tests passed.

Remote audit:

```bash
scripts/remote/proj169-2.sh picorv32-phase7d
```

The remote command executes the complete audit twice and compares the two
`release_manifest.json` files byte-for-byte.

## G0-G9 evidence

| Gate | Checked evidence |
| --- | --- |
| G0 | pinned PicoRV32 source is present and rehashes to its recorded digest |
| G1 | benchmark elaboration gate passed |
| G2 | mapped logic-only UltraScale+ synthesis gate passed |
| G3 | EmuIR contains the same 3,812 instances used downstream |
| G4 | 140 legal register-output cuts across exactly two FPGAs |
| G5 | all 140 sinks routed with zero link overload |
| G6 | all 140 bit-hops scheduled; 64 mapped cycles, zero mismatches |
| G7 | 140 logical lane records and 82 intentional virtual anchors agree |
| G8 | 4,223 merged cells have legal real OpenPARF placements |
| G9 | 4,223 cells routed, zero unrouted nets/DRC violations, +2.642 ns worst WNS |

Source evidence:

```text
picorv32.v
bytes=94,657
SHA-256=0836050971b3c6cdd28ac3b1e5719a67fb645161912bef1e472e63995ceb0622
```

## Sealed metrics

| Metric | Result |
| --- | ---: |
| source files rehashed | 1 |
| release artifacts hashed | 18 |
| original DUT cells | 3,812 |
| transport/controller cells | 411 |
| routed cells | 4,223 |
| cut nets | 140 |
| scheduled bit-hops | 140 |
| equivalent virtual cycles | 64 |
| nominal virtual frequency | 7.8125 MHz |
| worst routed WNS | +2.642 ns |

The 18-artifact inventory covers mapped synthesis JSON, global EmuIR,
partition assignment, system routes, TDM schedule, logical lane map, both
per-FPGA netlists, both OpenPARF placements, both mapped structural
netlists, both routed DCPs, runtime contract, timing XDC, physical summary,
and end-to-end QoR.

The two generated release manifests are byte-identical. Final manifest hash:

```text
15e226ab36bc7995dbddf26103204957a6905c4f4b05e9ab8649e9272b5fe7c9
```

## Completion boundary

Phase 7D completes the planned board-independent/virtual-board G0-G9 flow for
the validated logic-only, single-virtual-clock semantic envelope.

The manifest deliberately records `board_binding.status = virtual` and the
remaining requirements for package pins, dedicated clock-buffer binding, and
link training. G10, electrical pin validation, source-synchronous board IO
timing, bitstream generation, and hardware workload validation require a real
board BSP and are not silently inferred.

## NVDLA 731k-cell scale extension

The same auditor now passes the genuine connected
`NV_NVDLA_partition_a` run on the four-VU9P mesh. This experiment adds an
important multi-hop correction: cut-demand count, routed-sink count, and
scheduled bit-hop count are cross-checked separately. The result contains 3
cut demands and 3 sinks but 4 bit-hops.

The audit rehashes 376 source dependencies and 26 critical artifacts, checks
731,313 original cells, 74 transport cells, 731,387 routed mapped cells, one
whitelisted physical BUFGCE, zero route/DRC failures, and +1.435 ns worst
WNS. Two audits are byte-identical. The release manifest SHA-256 is:

```text
cc965f733830ec6a32c5357a516b36a96b160c8f85ac23ad26714507a713ef0a
```

See `docs/NVDLA_PARTITION_A_FULL_FLOW.md` for the full logical and physical
experiment record.
