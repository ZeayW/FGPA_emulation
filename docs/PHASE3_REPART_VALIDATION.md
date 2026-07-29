# Phase 3A RePart validation

## Scope

Phase 3A adds an external RePart provider for FPGA-aware multilevel
hypergraph partitioning. It replaces the optimization kernel while preserving
the `emuflow.partition-assignment/v1` contract and all independent EmuFlow
legality checks.

The integration is pinned to upstream RePart commit
`211a9d8fd526576387cad7ac6dd3531354aeb31c` and invokes the GPL-3.0-only
binary as a separate process. The EmuFlow patch adds `-r 0|1`; this validation
uses `-r 0` so that Phase 3A measures unique-owner partitioning. Logic
replication is introduced separately in Phase 3B with an explicit,
checker-visible artifact.

## Algorithm and interface

The provider exports the atomic EmuFlow netlist as RePart's native:

- `.are` eight-dimensional vertex weights;
- `.net` directed weighted hyperedges;
- `.info` per-FPGA resource limits;
- `.topo` physical FPGA adjacency and maximum hop distance; and
- `.fpga.out` assignment.

One RePart dimension is reserved for exact instance count. Up to seven active
physical resource dimensions occupy the remaining fields. A resource is
omitted only when the design uses it but every platform capacity is
unconstrained; all omissions are recorded in `repart_input.json`.

The requested multi-resource balance tolerance is converted into per-FPGA
capacity ceilings. EmuFlow relaxes the tolerance only to the lower bound
imposed by an indivisible atomic cluster or fixed assignment, and records both
requested and effective values. RePart's communication limit is deliberately
nonbinding in Phase 3A because the Phase 4 BoardDB checker remains the
authority for directional link capacity.

After import, EmuFlow independently checks:

- exact original-instance and cluster coverage;
- minimum used FPGA count;
- fixed and atomic-group constraints;
- per-resource physical capacity and declared balance ceilings;
- sequential-boundary cut legality; and
- transport-round classification.

## Reproduction

On `proj169-2`:

```sh
scripts/remote/proj169-2.sh repart-bootstrap
scripts/remote/proj169-2.sh test
scripts/remote/proj169-2.sh repart-phase3-smoke
scripts/remote/proj169-2.sh repart-phase3-picorv32
scripts/remote/proj169-2.sh repart-phase3-nvdla
scripts/remote/proj169-2.sh repart-nvdla-downstream
scripts/remote/proj169-2.sh repart-nvdla-phase7a
scripts/remote/proj169-2.sh repart-nvdla-phase7b
scripts/remote/proj169-2.sh repart-nvdla-phase7c-finalize
```

The remote binary is:

```text
/home/ziyiwang21/work/tools/repart-211a9d8/bin/repart
```

The NVDLA acceptance artifacts are rooted at:

```text
/data/zywang/emuflow/nvdla-repart-phase3a
```

## Unit and small/medium RTL results

The local and remote suites both pass 101 tests, including five focused
RePart tests for export, import, determinism, balance, and rejection of
replication records in partition-only mode.

| RTL | Instances | Clusters | Partition cells | Cut nets | Result |
| --- | ---: | ---: | --- | ---: | --- |
| synthesized counter | 6 | 5 | real two-FPGA split | 4 | pass |
| connected PicoRV32 | 3,812 | 12 | 349 / 3,463 | 140 | pass |
| PicoRV32 x32 | 121,984 | 384 | 64,804 / 57,180 | 0 | pass |

Two runs produced byte-identical assignment artifacts:

- counter:
  `ccaaf65903d040303728809dffadd07050c677a51e1088ef863c6c6576eed517`;
- connected PicoRV32:
  `e776839ce0e8fea316fddc0cd44c75d81a25585c8d7caede4f44ccf2144b03d4`;
- PicoRV32 x32:
  `8ef4de26d3b4cb1eed35da488f99c63e4c0ae088ea621f6c27af0ff59d073279`.

Connected PicoRV32 used 0.52 s and 46,316 KiB peak RSS, versus 0.69 s and
111,884 KiB for TritonPart. PicoRV32 x32 used 14.33 s and 907,640 KiB,
versus 14.58 s and 908,352 KiB. Both providers found the same cut counts on
these two cases; they serve as correctness and scale gates rather than the
primary QoR result.

## 731,313-cell NVDLA result

The acceptance design is the real synthesized `NV_NVDLA_partition_a` EmuIR:

- 731,313 mapped LUT/FF instances;
- 399,211 atomic clusters;
- four virtual XCvu9p FPGAs; and
- fixed input, BoardDB, clustering policy, and 10% requested balance
  tolerance for both providers.

| Metric | TritonPart baseline | RePart Phase 3A | Change |
| --- | ---: | ---: | ---: |
| cut nets | 142,882 | 67,680 | -52.63% |
| cut sink endpoints | 661,822 | 563,829 | -14.81% |
| illegal cuts | 0 | 0 | unchanged |
| balance violations | 0 | 0 | unchanged |
| used FPGAs | 4 | 4 | unchanged |

RePart partition loads are:

| FPGA | Instances | LUT | FF |
| --- | ---: | ---: | ---: |
| fpga0 | 268,329 | 122,741 | 145,588 |
| fpga1 | 213,224 | 122,741 | 90,483 |
| fpga2 | 46,216 | 26,648 | 19,568 |
| fpga3 | 203,544 | 62,392 | 141,152 |

The effective balance tolerance is 46.775833%, the same atomicity lower bound
reported for the frozen TritonPart baseline. There were no post-partition
balance repairs and no fixed-assignment repairs.

Two complete RePart executions were byte-identical:

```text
assignment SHA-256:
1d1b843c5248f68ad1f54a6011e6c8d42fb45aa6ae08110f25a7b1e5b887744f
```

Run 1 took 17:42.81 and run 2 took 17:22.86. Both used 23,424,968 KiB peak
RSS.

## Frozen downstream validation

The RePart assignment was passed unchanged through the existing Phase 4
router, Phase 5 scheduler, Phase 6 splitter/equivalence checker, and initial
Phase 7C runtime-contract generator.

| Metric | TritonPart baseline | RePart Phase 3A | Change |
| --- | ---: | ---: | ---: |
| route demands | 142,882 | 67,680 | -52.63% |
| routed remote sinks | 147,453 | 68,186 | -53.75% |
| routed bit-hops | 231,011 | 71,292 | -69.14% |
| maximum directed-link utilization | 44.9471% | 29.9149% | -15.0322 pp |
| scheduled hops | 231,011 | 71,292 | -69.14% |
| completion slot | 1,845 | 1,426 | -22.71% |
| TDM collisions | 0 | 0 | unchanged |

All route reachability/tree/capacity checks, TDM collision/precedence/value
simulation checks, exact 731,313-instance split coverage, endpoint agreement,
and two-cycle mapped equivalence checks passed. Phase 6 produced 802,425 net
segments, 142,584 transport endpoints, and 71,292 lane-map entries.

Measured downstream costs were:

| Stage | Elapsed | Peak RSS |
| --- | ---: | ---: |
| Phase 4 | 5.62 s | 515,824 KiB |
| Phase 5 | 15.55 s | 345,568 KiB |
| Phase 6 | 3:30.77 | 8,454,932 KiB |

## Physical-backend validation

Phase 7A synthesized the frozen transport schedule, merged it with each
partition, ran deterministic CPU OpenPARF global placement, and performed
discrete ArchitectureDB Site/BEL legalization. No partition, route, or TDM
decision was changed.

| FPGA | DUT cells | Transport cells | Placement cells | Sites used | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| fpga0 | 268,329 | 59,905 | 328,234 | 22,298 | legal |
| fpga1 | 213,224 | 38,225 | 251,449 | 24,842 | legal |
| fpga2 | 46,216 | 12,256 | 58,472 | 5,692 | legal |
| fpga3 | 203,544 | 7,959 | 211,503 | 12,247 | legal |
| total | 731,313 | 118,345 | 849,658 | 65,079 | pass |

Every partition uses provider
`openparf-global+emuflow-archdb-legalizer`; placement coverage exactly
matches lowering coverage. The complete Phase 7A run took 1:16:42 with
5,390,672 KiB maximum RSS.

| FPGA | Transport synthesis | OpenPARF | Architecture legalization |
| --- | ---: | ---: | ---: |
| fpga0 | 8:47.61 | 10:12.94 | 3:09.41 |
| fpga1 | 2:46.59 | 9:43.31 | 2:44.68 |
| fpga2 | 0:39.23 | 12:22.33 | 1:36.97 |
| fpga3 | 0:21.37 | 10:31.16 | 2:14.87 |

The sparse `fpga2` placement needed 429 iterations before both normalized
resource overflows fell below 20%; the other global placements stopped at
342, 330, and 358 iterations for `fpga0`, `fpga1`, and `fpga3`,
respectively.

Vivado route and final physical/runtime audit results are recorded here after
the four-checkpoint Phase 7B/7C run completes.

## Conclusion and limitations

Phase 3A demonstrates a material partition and frozen-downstream improvement
on the large real RTL acceptance design without changing the input,
clustering, balance lower bound, BoardDB, router, TDM scheduler, or splitter.

This result does not claim timing-driven partitioning or logic replication.
RePart uses its fixed upstream seed 42, and its eight resource fields limit the
interface to cells plus seven active physical dimensions. Phase 3B adds legal
combinational replication; Phase 4 later introduces timing-aware board-level
routing under fixed Phase 3 output.
