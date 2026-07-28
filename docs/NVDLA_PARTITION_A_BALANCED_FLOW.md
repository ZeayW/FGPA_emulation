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
```

Validation date: 2026-07-28.

Phases 3-6 and the initial Phase 7C runtime contract pass on `proj169-2`.
Phase 7A physical-backend execution is recorded separately below as it
completes. The experiment root is:

```text
/data/zywang/emuflow/nvdla-balanced-phase3b
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

Its physical status remains `pending` until all four FPGA implementations are
independently reopened and checked.

## Phase 7A status

Phase 7A is executing on `proj169-2`. Unlike the earlier deliberately
unbalanced run, all four partitions now contain substantial original logic
and tens of thousands of source/shadow transport signals. The first target is
`fpga3`, which contains 107,055 original instances, 42,218 source signals,
44,770 shadow signals, 114,743 transport endpoints, and 6.20 MB of generated
transport SystemVerilog.

The real `fpga3` transport RTL synthesizes successfully into 94,242
primitives: 49,460 LUTs and 44,782 FFs. Yosys takes 11:10.56 and peaks at
2,217,224 KiB RSS. The merged placement IR contains:

| Metric | `fpga3` result |
| --- | ---: |
| original instances | 107,055 |
| transport instances | 94,242 |
| merged instances | 201,297 |
| merged LUT | 116,172 |
| merged FF | 85,125 |

This run exposed a lowering scalability bug: each source/shadow interface bit
performed a full scan of every transport net. For `fpga3` that implied 86,988
lookups over 136,527 nets. The implementation now builds one
`(port, bit, direction) -> net` index and performs three total net-list passes
independent of interface width. The indexed real lowering completes in 41.62
seconds with 2,067,756 KiB peak RSS. The Phase 7A runner now checkpoints
transport synthesis/import separately, so the accepted 11-minute Yosys result
was reused while validating the fix.

Final transport-cell, OpenPARF placement, Vivado routing, DRC, and timing
metrics will be added only after their independent gates complete.

## Reproduction

Starting from the checked Phase 1 EmuIR:

```bash
scripts/remote/nvdla_partition_a_balanced.sh logical ROOT PHASE1_IR
scripts/remote/nvdla_partition_a_balanced.sh phase7a ROOT
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
