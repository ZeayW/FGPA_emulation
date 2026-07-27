# Phase 3 multi-FPGA partitioning validation

## Result

Phase 3 is implemented and passed G4 on `proj169-2` with two complementary
real open-RTL runs:

```text
EmuIR
  -> combinationally indivisible atomic clusters
  -> group/fixed/hard-macro closure
  -> weighted hMETIS hypergraph
  -> OpenROAD/TritonPart multilevel partitioning
  -> common partition-assignment artifact
  -> cut-net extraction
  -> independent coverage/capacity/constraint/cut checker
```

Validation date: 2026-07-27.

The 121,984-cell PicoRV32 x32 harness validates scale, exact coverage,
capacity, and deterministic balance. A connected 3,812-cell PicoRV32 validates
that a real sequential design can be forced across two FPGAs using only legal
register-output cuts.

## Implemented artifacts and commands

Phase 3 adds these versioned artifacts:

- `emuflow.clusters/v1`: atomic cluster membership, resources, groups, and
  fixed-FPGA closure;
- `emuflow.partition-constraints/v1`: exact/glob groups, fixed assignments,
  required FPGA count, and balance tolerance;
- `emuflow.partition-assignment/v1`: cluster and instance assignments,
  per-FPGA resources/utilization, cut nets, and recomputable metrics;
- `emuflow.phase3-report/v1`: the pipeline result and independent checker
  summary;
- `emuflow.tritonpart-input/v1`: vertex/FPGA ordering, multi-dimensional
  weights, legal hyperedges, balance relaxation, and exact tool artifacts;
- weighted `partition.hgr`, `partition.fix`, generated Tcl, OpenROAD log, and
  `.part` solution under the Phase 3 `tritonpart/` directory.

The CLI entry points are:

```bash
PYTHONPATH=src python3 -m emuflow phase3 \
  --ir design.emuir.json \
  --platform platforms/virtual/xcvu3p_2fpga_p2p.json \
  --out build/phase3 \
  --provider tritonpart \
  --openroad /path/to/openroad \
  --seed 20260727

PYTHONPATH=src python3 -m emuflow partition validate \
  build/phase3/assignment.json \
  --clusters build/phase3/clusters.json \
  --ir design.emuir.json \
  --platform platforms/virtual/xcvu3p_2fpga_p2p.json
```

The remote regression is:

```bash
scripts/remote/proj169-2.sh picorv32-x32-phase3
```

## Partition semantics

The current synchronous semantic envelope permits inter-FPGA cuts only at
register outputs and replicated primary inputs. Clock and reset nets are
modeled as replicated globals.

Phase 3 uses union-find closure to make every forbidden-cut connectivity
component atomic. It also unions explicit user groups and connected hard
macros carrying BRAM, URAM, DSP48, or CARRY8 resources. A cluster can be fixed
to an FPGA; conflicting fixed constraints fail before assignment.

The default provider exports one hypergraph vertex per atomic cluster. Vertex
dimension zero is instance count; remaining dimensions are active resources
supported by every target FPGA, such as LUT and FF. Each register-output net
connecting at least two clusters becomes a weighted hyperedge. Fixed clusters
are emitted in hMETIS fixed-vertex format.

TritonPart receives the fixed seed, multi-dimensional weights, per-part base
balance, and at least one vertex per part. Because its imbalance is a hard
constraint, EmuFlow computes a deterministic lower bound from the largest
atomic/fixed cluster and automatically relaxes an infeasible user target.
Connected PicoRV32 therefore uses 41.80712% rather than the requested 10%;
without this relaxation, its 3,463-cell atomic cone cannot fit either nominal
50% block.

The dependency-free deterministic greedy implementation remains selectable
with `--provider greedy`. It is used for unit tests, environments without
OpenROAD, and provider A/B comparisons; it is no longer the default.

The checker independently reloads EmuIR and BoardDB, then recomputes exact
instance coverage, cluster indivisibility, resource totals, effective-capacity
fit, group/fixed constraints, used FPGA count, forbidden cuts, cut-net
endpoints, and cut metrics. It does not trust summaries emitted by the
partition provider.

## 121,984-cell scale result

| Metric | Result |
| --- | ---: |
| mapped instances | 121,984 |
| atomic clusters | 384 |
| used FPGAs | 2 |
| partition cells | 60,992 / 60,992 |
| partition LUT | 35,440 / 35,440 |
| partition FF | 25,552 / 25,552 |
| LUT effective-capacity utilization | 11.991% / 11.991% |
| FF effective-capacity utilization | 4.323% / 4.323% |
| illegal cuts | 0 |
| TritonPart hypergraph | 384 vertices / 4,480 hyperedges |
| runtime | 14.58 s |
| peak RSS | 908,352 KiB |
| artifact size | 20 MB |
| assignment SHA-256 | `c888ecf9902a0c31cc82aa5dc35502589fd4d450e373a452c9274037719a5a66` |

The x32 harness has no inter-core data connections. The best legal solution
places complete cores on each FPGA and therefore produces zero inter-FPGA cut
nets. That is a positive partition-quality result, but it cannot validate the
cut-net artifact by itself.

## Connected PicoRV32 cut result

| Metric | Result |
| --- | ---: |
| mapped instances | 3,812 |
| atomic clusters | 12 |
| used FPGAs | 2 |
| partition cells | 3,463 / 349 |
| partition LUT | 1,997 / 218 |
| partition FF | 1,466 / 131 |
| cross-FPGA nets | 140 |
| remote sink endpoints | 245 |
| cut class | 140 register-output |
| replicated primary inputs / global nets | 1 / 1 |
| illegal cuts | 0 |
| TritonPart hypergraph | 12 vertices / 140 hyperedges |
| effective imbalance | 41.80712% |
| runtime | 0.69 s |
| peak RSS | 111,884 KiB |
| artifact size | 504 KB |
| assignment SHA-256 | `7211089179abf512b7cfb2d4f76d221552cd3ecedc416ab72233e1fea278a2d2` |

Both runs were executed twice with seed `20260727`; each pair of complete
`assignment.json` files had identical SHA-256 hashes. Both assignments also
passed a separate CLI checker invocation.

## TritonPart versus greedy

| Design/provider | Runtime | Peak RSS | Partition cells | Cut nets |
| --- | ---: | ---: | ---: | ---: |
| x32 TritonPart | 14.58 s | 908,352 KiB | 60,992 / 60,992 | 0 |
| x32 greedy | 13.10 s | 907,264 KiB | 60,992 / 60,992 | 0 |
| connected TritonPart | 0.69 s | 111,884 KiB | 3,463 / 349 | 140 |
| connected greedy | 0.41 s | 43,932 KiB | 3,463 / 349 | 140 |

The x32 cluster assignments differ, but both providers place whole
independent cores and therefore achieve the same zero-cut objective. The
connected assignments are identical because the register-output-only
semantics leave one dominant 3,463-cell atomic cluster. These results validate
the provider integration, determinism, resource legality, and scale; they do
not yet demonstrate a TritonPart QoR advantage on a large connected design.

## Experimental findings

1. A 100k-cell replicated harness alone is insufficient for a cut-flow test:
   independent cores partition with zero communication.
2. Connected PicoRV32 exposes a 3,463-cell atomic sequential cone under the
   register-output-only cut rule. A more even split would require a forbidden
   combinational cut, so the legal 3,463/349 result is intentionally
   imbalanced.
3. The initial experimental gate required at least 1,000 cells per connected
   partition. The checker correctly rejected that infeasible expectation.
   The final gate requires both partitions to be non-trivial while preserving
   cut legality.
4. The 121,984-cell run remains exactly balanced because its independent core
   components can be assigned without cuts.

## G4 acceptance

| Requirement | Evidence |
| --- | --- |
| every primitive belongs to exactly one partition | 121,984/121,984 and 3,812/3,812 independently recomputed |
| group and fixed constraints hold | normalized constraints, unit tests, independent checker |
| no forbidden combinational cut | zero illegal cuts in both real-design runs |
| every FPGA fits effective capacities | resource totals recomputed from EmuIR |
| reproducible fixed-seed metrics | byte-identical assignment JSON and matching SHA-256 |

Local and remote regression suites contain 66 passing tests, including
infeasible capacity, missing coverage, split atomic cluster, group/fixed
constraint, deterministic assignment, weighted hypergraph export, malformed
solution rejection, external-provider execution, and forced two-FPGA cases.

## Remaining limitations and Phase 4 handoff

The pinned validated tool is OpenROAD `v2.0-17598-ga008522d8`. Its hypergraph
command runs TritonPart multilevel optimization but explicitly reports that
native timing-driven mode is unavailable. EmuFlow accepts optional
`emuflow.partition-net-weights/v1` hyperedge weights, but no Vivado/OpenSTA
criticality extractor is implemented yet. SLR-aware cost, BoardDB/TDM
feedback, and a large genuinely connected benchmark remain QoR work.

TritonPart's hypergraph interface uses one base-balance ratio per block for
all vertex dimensions. The adapter therefore supports homogeneous or
proportionally scaled FPGA capacities and rejects resource-specific
heterogeneous capacity ratios rather than silently mis-modeling them.

The TritonPart connected assignment passed the existing downstream gates:
Phase 4 routed all 140 demands with zero overload, Phase 5 scheduled 140
bit-hops with zero collisions, and Phase 6 compared 102,208 state bits plus
12,864 output bits over 64 virtual cycles with zero mismatches.
