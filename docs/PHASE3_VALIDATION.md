# Phase 3 multi-FPGA partitioning validation

## Result

Phase 3 is implemented and passed G4 on `proj169-2` with two complementary
real open-RTL runs:

```text
EmuIR
  -> combinationally indivisible atomic clusters
  -> group/fixed/hard-macro closure
  -> deterministic multi-resource assignment
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
  summary.

The CLI entry points are:

```bash
PYTHONPATH=src python3 -m emuflow phase3 \
  --ir design.emuir.json \
  --platform platforms/virtual/xcvu3p_2fpga_p2p.json \
  --out build/phase3 \
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

The dependency-free provider uses deterministic multi-resource greedy
assignment with:

- effective BoardDB capacities and utilization headroom;
- proportional per-resource balance targets;
- register-output connectivity as cut cost;
- a stable SHA-256 seed tie-break;
- forced use of the requested number of FPGAs.

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
| runtime | 14.25 s |
| peak RSS | 907,628 KiB |
| artifact size | 19 MB |
| assignment SHA-256 | `1237c796df416f29f29bdad572eff7ec6c5c0ed9935f0f52052469dd10e42fb0` |

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
| runtime | 0.45 s |
| peak RSS | 42,460 KiB |
| artifact size | 448 KB |
| assignment SHA-256 | `dcb84aef5b9988478389fc16a2b858b6b055d5cfaf313684c9b3451b2670498d` |

Both runs were executed twice with seed `20260727`; each pair of complete
`assignment.json` files had identical SHA-256 hashes. Both assignments also
passed a separate CLI checker invocation.

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

Local and remote regression suites contain 35 passing tests, including
infeasible capacity, missing coverage, split atomic cluster, group/fixed
constraint, deterministic assignment, and forced two-FPGA cases.

## Remaining limitations and Phase 4 plan

Phase 3 is a deterministic dependency-free baseline, not yet a TritonPart QoR
integration. It lacks timing weights, SLR-aware cost, true cascade metadata,
cluster refinement moves, and an optional external partition provider.

Phase 4 will consume the 140 connected-PicoRV32 cut nets and implement:

1. `emuflow.system-routes/v1`;
2. BoardDB directed-link graph construction;
3. deterministic shortest-path unicast and multicast trees;
4. negotiated congestion with historical link cost;
5. independent reachability, acyclicity, direction, latency, and capacity
   reconstruction;
6. connected PicoRV32 validation on the current two-FPGA link, followed by a
   synthetic multi-hop virtual BoardDB topology to exercise routing choices.
