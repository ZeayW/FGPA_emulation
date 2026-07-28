# NVDLA partition-A balanced four-FPGA validation

## Scope and status

This experiment replaces the earlier backend-coverage-only NVDLA assignment
with a resource-bounded four-FPGA assignment and carries it through the
board-independent flow:

```text
731,313-cell EmuIR
  -> 399,211 legal atomic clusters
  -> OpenROAD/TritonPart multilevel hypergraph partitioning
  -> deterministic multi-resource balance legalization
  -> independent Phase 3 reload/check
  -> system multicast routing
  -> two-round TDM assignment and transport simulation
  -> four per-FPGA netlists, transport RTL, and mapped equivalence
  -> independent Phase 6 reload/check
  -> Phase 7 transport synthesis and OpenPARF placement
  -> Vivado placement, routing, DRC, and 250 MHz fabric timing closure
  -> independent four-DCP Phase 7C runtime/QoR closure
```

Validation date: 2026-07-28 through 2026-07-29.

Phases 3-7C pass on `proj169-2` for the real 731,313-cell design. The
experiment root is:

```text
/data/zywang/emuflow/nvdla-balanced-phase3b/balanced-flow
```

The input is the official NVDLA `NV_NVDLA_partition_a` design already
described in `docs/NVDLA_PARTITION_A_FULL_FLOW.md`:

- source repository: `https://github.com/nvdla/hw`;
- pinned commit: `8e06b1b9d85aab65b40d43d08eec5ea4681ff715`;
- mapped instances: 731,313;
- LUTs: 334,522;
- FFs: 396,791;
- black boxes: 0;
- target: four virtual `xcvu9p-flga2104-2L-e` devices in a 2-by-2 mesh.

## Sequential cut semantics

The original register-output-only model made each complete combinational cone
between FF banks indivisible. NVDLA consequently contained one dominant
130,394-cell cluster with 122,741 LUTs and 7,653 FFs.

The implemented model now permits two transported sequential boundaries:

1. `register_output`: distribute stable FF Q values in transport round 0;
2. `register_input`: distribute LUT-to-FF D/CE values in transport round 1.

Round 1 starts only after every round-0 value has arrived plus one fabric
settle slot. Asynchronous clear/preset inputs remain outside this cut class.
Clock, reset, and primary-input nets remain replicated globals rather than
TDM payload.

The regenerated real NVDLA EmuIR contains:

| Cut class | Nets |
| --- | ---: |
| register output | 396,791 |
| register input | 55,676 |
| combinational | 278,846 |
| primary input | 2,966 |
| clock | 4 |
| reset | 2 |

Only 55,676 net classifications changed relative to the original import. The
resulting closure has 399,211 clusters rather than 5,938, while still
preserving the 130,394-cell dominant atomic region.

## TritonPart constraint semantics

EmuFlow defines `balance_tolerance` relative to a target block share. For
example, 10% around a 25% target allows at most 27.5% of a resource.
OpenROAD TritonPart's hypergraph command instead adds
`balance_constraint * 0.01` directly to the target share. Therefore the
adapter must translate a relative percentage into percentage points:

```text
TritonPart UBfactor = target share x EmuFlow effective tolerance percent
```

The largest atomic LUT cluster occupies 36.693958% of all LUTs, so the
requested 10% relative tolerance is structurally infeasible. EmuFlow
automatically raises the effective relative tolerance to 46.775833%, which
corresponds to a TritonPart UBfactor of 11.693958 percentage points. The
resulting independently checked upper limits per FPGA are:

| Dimension | Upper limit |
| --- | ---: |
| cells | 268,347 |
| LUT | 122,749 |
| FF | 145,598 |

This auto-relaxation is an atomicity lower bound, not a claim of equal loads.

## Best-effort solution and balance legalization

OpenROAD `v2.0-17598-ga008522d8` reads the real 399,211-vertex,
442,681-hyperedge, three-dimensional hypergraph. Its raw seed-20260727
solution has a low cut but violates the independent LUT/FF upper bounds:

| FPGA | Raw cells | Raw LUT | Raw FF |
| --- | ---: | ---: | ---: |
| fpga0 | 226,185 | 78,349 | 147,836 |
| fpga1 | 374,596 | 207,992 | 166,604 |
| fpga2 | 97,254 | 46,838 | 50,416 |
| fpga3 | 33,278 | 1,343 | 31,935 |

The adapter no longer accepts a successful OpenROAD exit code as sufficient.
Every returned assignment is checked against EmuFlow's independently
recomputed multidimensional limits. Invalid seed results are rejected.

With `--tritonpart-repair-balance`, a deterministic legalizer starts from the
low-cut TritonPart solution and moves only clusters needed to remove upper
violations. For each overloaded source it ranks movable clusters by
hypergraph cut delta per normalized overload relief, chooses a destination
that remains within every upper bound, updates incident-edge cut state, and
repeats until no upper violation remains. Fixed clusters are never moved.

The repair moved 39,056 of 399,211 clusters:

| Transition | Clusters moved |
| --- | ---: |
| fpga0 -> fpga1 | 1,166 |
| fpga0 -> fpga2 | 445 |
| fpga0 -> fpga3 | 596 |
| fpga1 -> fpga2 | 28,614 |
| fpga1 -> fpga3 | 8,235 |

The ordered move trace SHA-256 is
`853330971035ad3b1a7c16ada77ead1e1b7cdccf8afb521ae723a63963adbc92`.
The final assignment is:

| FPGA | Cells | LUT | FF |
| --- | ---: | ---: | ---: |
| fpga0 | 223,669 | 78,071 | 145,598 |
| fpga1 | 258,216 | 122,749 | 135,467 |
| fpga2 | 142,373 | 66,990 | 75,383 |
| fpga3 | 107,055 | 66,712 | 40,343 |

Independent Phase 3 reload validation reports:

- 731,313/731,313 exact instance coverage;
- 399,211/399,211 exact cluster coverage;
- four used FPGAs;
- zero illegal cuts;
- zero multidimensional balance violations;
- maximum upper-limit ratio `0.999997775257198`.

The repaired cluster assignment SHA-256 is
`ebdd0f6f00412e8f086f2f78f58b8377a03b736ee604c26555b5164503b58948`.
The formally imported run and the independent prototype produce identical
cluster assignments.

## Cut quality and controls

| Provider/result | Cut hyperedges |
| --- | ---: |
| raw TritonPart, independently infeasible | 79,622 |
| TritonPart plus balance legalization | 142,882 |
| upper-bound-feasible greedy baseline | 320,732 |

Legalization adds an audited estimated cut delta of 63,260, exactly matching
the independently recomputed final cut. The final result reduces cuts by
55.45% relative to the feasible greedy baseline.

Additional direct TritonPart seeds returned all four labels but still
violated independent multidimensional upper bounds. Two exploratory runs
using the greedy assignment as a community file were stopped after roughly
20 minutes without producing a solution; they are not acceptance evidence.

The installed hypergraph interface explicitly warns that timing-driven mode
is unsupported and disables timing awareness. Current hyperedge weights are
therefore unit cut weights. EmuFlow accepts external net weights, but this run
does not claim timing-driven partitioning.

## Phases 4 and 5

The 4,096-slot routing budget is the smallest tested production setting that
comfortably holds both transport rounds:

| Metric | Result |
| --- | ---: |
| cut demands | 142,882 |
| routed remote sinks | 147,453 |
| multicast tree bit-hops | 231,011 |
| routing iterations | 1 |
| overloaded directions | 0 |
| maximum directed-link utilization | 44.9471% |
| Phase 4 wall time / peak RSS | 12.08 s / 670,052 KiB |

Phase 5 uses a successor-set earliest-slot allocator. Each capacity domain
maintains the fill count for every slot and a disjoint-set successor pointing
to the next non-full slot. This preserves the deterministic earliest legal
schedule while avoiding a repeated scan from slot zero for every bit-hop.

| Metric | Result |
| --- | ---: |
| scheduled bit-hops | 231,011 |
| frame slots | 4,096 |
| transport rounds / barriers | 2 / 1 |
| completion slot | 1,845 |
| collisions | 0 |
| simulated frames | 16 |
| delivered sink values | 2,359,248 |
| simulation mismatches | 0 |
| Phase 5 wall time / peak RSS | 47.95 s / 861,388 KiB |

The transport trace SHA-256 is
`ff13427922c115d6f7555bd9984d8e774877c593ea8de19dd247f1a9c568dae5`.

## Phase 6 and runtime contract

Phase 6 writes four explicit netlists and transport endpoints:

| Metric | Result |
| --- | ---: |
| original instances | 731,313 |
| net segments | 881,698 |
| scheduled lane-map entries | 231,011 |
| TX/RX transport endpoints | 462,022 |
| virtual anchors | 512 |
| unbound package pins | 512 |
| split coverage/agreement errors | 0 |

The independently reloaded `split validate` command reports the same counts.
The complete artifact directory is about 2.0 GiB. Phase 6 takes 4:14.79 and
peaks at 9,964,532 KiB RSS.

Mapped LUT/FF cycle equivalence covers 731,313 primitives, 396,791 FFs,
334,522 LUTs, 793,582 compared state bits, and 1,102 output bits over two
virtual DUT cycles. Both rounds are modeled, including 21,557 transported
register-input cuts and 21,557 round-barrier checks. There are zero
mismatches. The trace SHA-256 is
`f088c2f4f651c8b93cf4278ae5a65e7bc90ca81499ba94580de3146680dfad50`.

The initial Phase 7C runtime contract passes its logical checks:

- 250 MHz fabric clock;
- 4,096 slots per virtual frame;
- slot 1,845 completion;
- slot 4,095 barrier release;
- 2,250 shadow-settle slots;
- nominal virtual DUT frequency 0.06103515625 MHz.

The physical implementation closes DUT logic at a deliberately stricter
256 ns period while the one-edge-per-4,096-slot runtime has a 16,384 ns
nominal virtual period. Phase 7C accepts a backend period only when it is no
slower than the runtime period; the physical result therefore has 64x
conservative DUT timing headroom.

## Phase 7A status

Phase 7A completes on `proj169-2` for all four balanced partitions. Unlike
the earlier deliberately unbalanced run, every partition contains substantial
original logic and tens of thousands of source/shadow transport signals.
Real Yosys synthesis, EmuIR import, indexed lowering, OpenPARF global
placement, and ArchitectureDB legalization all exit normally.

| FPGA | Original cells | Transport cells | Merged cells | Merged LUT | Merged FF | Merged nets |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `fpga0` | 223,669 | 95,404 | 319,073 | 112,396 | 206,677 | 267,689 |
| `fpga1` | 258,216 | 124,793 | 383,009 | 147,895 | 235,114 | 383,974 |
| `fpga2` | 142,373 | 71,652 | 214,025 | 113,091 | 100,934 | 210,327 |
| `fpga3` | 107,055 | 94,242 | 201,297 | 116,172 | 85,125 | 175,056 |
| **Total** | **731,313** | **386,091** | **1,117,404** | **489,554** | **627,850** | **1,037,046** |

The transport synthesis scale ranges from 71,652 to 124,793 primitives per
FPGA. For example, `fpga3` contains 42,218 source signals, 44,770 shadow
signals, 114,743 transport endpoints, and 6.20 MB of generated transport
SystemVerilog. Its transport maps to 94,242 primitives: 49,460 LUTs and
44,782 FFs. Yosys takes 11:10.56 and peaks at 2,217,224 KiB RSS.

This run exposed a lowering scalability bug: each source/shadow interface bit
performed a full scan of every transport net. For `fpga3` that implied 86,988
lookups over 136,527 nets. The implementation now builds one
`(port, bit, direction) -> net` index and performs three total net-list passes
independent of interface width. The indexed real lowering completes in 41.62
seconds with 2,067,756 KiB peak RSS. The Phase 7A runner now checkpoints
transport synthesis/import separately, so the accepted 11-minute Yosys result
was reused while validating the fix.

All four balanced partitions use OpenPARF for continuous global coordinates
followed by the checked ArchitectureDB legalizer. This is the scalable path
previously used for the 731,331-cell unbalanced partition; OpenPARF's direct
detailed legalizer is retained for genuinely small partitions but is not the
default for these 201K–383K-cell merged partitions.

The strategy was selected from a real `fpga3` pilot. Direct OpenPARF global
placement reached 14.83% LUT and 19.98% FF overflow in 601.624 seconds, then
ripped up 192,057 of 201,297 instances for detailed legalization. That
legalizer was still running without a result more than six minutes later, so
the pilot was intentionally stopped and its log retained as
`phase7a-direct-pilot.stderr.log`. Transport synthesis, import, lowering, and
Phase 2 reference checkpoints remain valid and are reused by the global-mode
run.

| FPGA | Final LUT/FF overflow | OpenPARF wall time | Architecture legalization | Legal sites | Cells covered |
| --- | ---: | ---: | ---: | ---: | ---: |
| `fpga0` | 0.94% / 19.59% | 9:02.34 | 2:48.29 | 18,733 | 319,073 |
| `fpga1` | 5.65% / 19.96% | 10:14.44 | 3:25.57 | 24,650 | 383,009 |
| `fpga2` | 15.64% / 19.57% | 10:08.68 | 2:26.81 | 18,849 | 214,025 |
| `fpga3` | 14.83% / 19.98% | 10:05.20 | 2:16.45 | 19,362 | 201,297 |
| **Total** |  |  |  | **81,594** | **1,117,404** |

Each accepted Phase 2 report names
`openparf-global+emuflow-archdb-legalizer` as its provider and reports a
legal placement with exact lowering-instance coverage. The Phase 7A aggregate
gate cross-checks all four reports and prints:

```text
EMUFLOW_NVDLA_PHASE7A status=pass merged_cells=1117404 transport_cells=386091
```

Four separate `placement validate` invocations then reload the ArchitectureDB,
merged EmuIR, and placement JSON. All four again report `legal` with the exact
cell/site counts above; their wall times range from 36.38 to 47.65 seconds.

A concurrent `fpga1` transport pre-synthesis finished at the same moment the
main runner inspected its files, starting one redundant Yosys process. The
redundant task was stopped; the accepted 133.1 MB mapped JSON was re-imported
independently and reproduced the 251.3 MB EmuIR byte for byte with SHA-256
`f1813748f4e7edf106901beb94620cc1d7c51284b58c79a9ee3e5202e16fbfff`.
The resume path now validates status, provider, legal placement, and exact
cell coverage before skipping a completed partition.

## Phase 7B Vivado placement, routing, and timing closure

Vivado 2025.2 reads each structural mapped netlist, verifies the exact
OpenPARF/ArchitectureDB cell order, fixes a deterministic sparse subset of
LUT Site/BEL anchors, and leaves the remaining LUTs and all FFs movable.
`SSI_SpreadLogic_high` performs congestion-aware placement and `route_design`
produces a fully routed out-of-context VU9P checkpoint.

The `ff_loc_repairs` marker counts FFs intentionally freed from the coarse
OpenPARF anchors; it is not a failed-cell count. Exact mapped identities are
checked again after placement and routing.

| FPGA | Mapped cells | Physical cells | Fixed LUT anchors | Fully routed / logical nets | DRC | WNS / TNS / WHS (ns) | Accepted wall time / peak RSS |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `fpga0` | 319,073 | 319,073 | 293 | 256,984 / 267,691 | 0 | +0.122 / 0 / +0.040 | 47:34.97 / 7,818,764 KiB |
| `fpga1` | 383,009 | 383,156 | 384 | 372,814 / 384,123 | 0 | +0.010 / 0 / +0.021 | 2:04:36 / 6,906,880 KiB |
| `fpga2` | 214,025 | 214,025 | 295 | 199,737 / 210,329 | 0 | +0.453 / 0 / +0.019 | 38:45.06 / 6,749,192 KiB |
| `fpga3` | 201,297 | 201,297 | 302 | 170,949 / 175,058 | 0 | +0.047 / 0 / +0.043 | 25:57.39 / 6,644,400 KiB |

The initial four-part route took 3:11:20 wall time and peaked at 8,535,612
KiB RSS. Three partitions met the 4 ns fabric constraint immediately.
`fpga1` was fully routed and DRC-clean but failed setup with WNS
-0.648 ns, TNS -479.400 ns, and 4,333 failing endpoints.

The worst path is generated transport control, not NVDLA DUT logic. It runs
from the virtual runtime slot counter to a shadow-register CE, has three LUT
levels, and spends 4.287 of 4.543 ns in routing. One decoder net,
`__emuflow_net_1226`, has fanout 1,095 and contributes 1.867 ns in the
default route.

A post-route `AggressiveFanoutOpt` plus `AggressiveExplore` pass reduces the
result to WNS -0.471 ns, TNS -127.196 ns, and 1,576 failing endpoints without
adding or dropping cells. Repeating the same strategy reaches only
-0.458 ns before rerouting and is stopped when its routed intermediate value
degrades to -0.463 ns. Vivado rejects forced replication in post-route mode,
so the accepted flow starts from `placed.dcp`.

The timing-close pass first forces replication of the measured 1,095-fanout
decoder, then runs pre-route `AggressiveExplore` and an aggressive route.
Vivado creates 146 audited replicas:

| Replica type | Count |
| --- | ---: |
| FDRE | 11 |
| LUT2 | 66 |
| LUT3 | 5 |
| LUT4 | 64 |

No baseline cell is missing or changes primitive type. Router overlap
convergence begins at 92,929 and reaches zero; timing-driven iterations
improve setup through -0.229, -0.181, and -0.022 ns before post-hold cleanup
closes at WNS +0.010 ns, TNS 0, and WHS +0.021 ns. The final route has no
critical warnings or errors and 0 DRC checks.

The original failing checkpoint is retained as `routed-default.dcp`.
The accepted checkpoint and replica manifest SHA-256 values are:

```text
b96c2f91d1d0ec7f6f6e5eb49a1454ec2019620349fbc952ef93746964bb904d  routed.dcp
17585734485fd9ac8d085db0ff9cb096d25f946c5ec6d9f168e30d92d388b770  timing_optimization_cells.tsv
```

## Phase 7C independent physical and runtime closure

Phase 7C opens all four promoted DCPs in fresh Vivado processes. It does not
trust the route process's in-memory state. Each checker independently
requires:

- every mapped cell identity to be present;
- every non-clock extra cell to match the exact timing-optimization manifest;
- only explicitly recognized BUFG clock infrastructure outside that manifest;
- no unrouted nets, no DRC checks, and non-negative WNS;
- the 4 ns fabric period and a DUT period no slower than the 16,384 ns runtime
  period.

All four checks pass. The final aggregate contains 1,117,404 mapped cells and
1,117,551 physical cells. The 147-cell difference is exactly 146 timing
replicas plus one BUFG inserted in `fpga1`. There are zero unrouted nets,
zero DRC violations, and the worst cross-FPGA physical WNS is +0.010 ns.

The regenerated runtime testbench executes 64 virtual frames across four
controllers, injects three barrier-stall cycles, and reports:

```text
EMUFLOW_RUNTIME_TB status=pass frames=64 stalled_cycles=3 controllers=4
EMUFLOW_NVDLA_PHASE7C status=pass routed_cells=1117404 \
physical_cells=1117551 infrastructure_cells=147 optimization_cells=146 \
original_cells=731313 transport_cells=386091 unrouted_nets=0 \
drc_violations=0 worst_wns_ns=0.01
```

The resulting `phase7c_report.json` and `qor_report.json` both have
`status: pass`.

## Reproduction

Starting from the checked Phase 1 EmuIR:

```bash
scripts/remote/nvdla_partition_a_balanced.sh logical ROOT PHASE1_IR
scripts/remote/nvdla_partition_a_balanced.sh phase7a ROOT
scripts/remote/nvdla_partition_a_balanced.sh phase7b ROOT
scripts/remote/nvdla_partition_a_balanced.sh phase7b-timing-close ROOT
scripts/remote/nvdla_partition_a_balanced.sh phase7c-finalize ROOT
EMUFLOW_NVDLA_SYNTHESIS_DIR=ORIGINAL_SYNTHESIS \
  scripts/remote/nvdla_partition_a_balanced.sh phase7d ROOT
```

The logical script executes real TritonPart rather than importing the
precomputed raw solution, runs independent Phase 3 and Phase 6 reload
validators, and creates the initial Phase 7C runtime/timing contract.

## Board boundary

The target remains a virtual four-VU9P mesh. OpenPARF and Vivado can validate
FPGA-internal implementation without a selected board, but package pin,
IOSTANDARD, clock-source, link-training, signal-integrity, bitstream, and
hardware-execution closure require a concrete board BSP. The 512 unbound
virtual anchors make this boundary explicit.
