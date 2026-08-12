# EmuFlow validation requirements

These requirements apply to all work in this repository.

## README synchronization is mandatory

- Before every push, review all commits being pushed for README impact without
  waiting for a user reminder.  Treat this review as a required pre-push gate.
- Any user-visible change to behavior, defaults, CLI options, schemas,
  providers, algorithms, validation requirements, benchmark availability,
  completion status, artifact locations, limitations, or recommended commands
  must update `README.md` in the same push.  Do not push the implementation
  first and leave documentation for a later milestone.
- README status and QoR statements must describe only evidence that actually
  exists.  Mark incomplete validation as pending or blocked; never infer final
  Phase 7 results from an intermediate artifact.
- If the review concludes that a push has no README impact, state
  `README reviewed: no user-visible change` in the handoff or push summary.
  This exception is for genuinely internal or mechanically equivalent changes,
  not a reason to omit documentation for a new capability or changed contract.
- After every push, verify that the remote branch contains both the intended
  implementation commits and the required README update.  If concurrent work
  advanced the branch, rebase safely and repeat the verification; never force
  push over unrelated work.

## End-to-end acceptance is mandatory

- A Phase 6 algorithm, provider, optimization, or default-selection change is
  not complete when only Phase 6 artifacts or proxy metrics have passed.  The
  required acceptance endpoint is the completed physical Phase 7 flow.
- The primary QoR results are the final aggregate WNS and TNS after Phase 7.
  Phase 6 metrics such as crossing bits, SLL crossings, grouping objective,
  RUDY, position SSE, estimated wirelength, and pin distance are diagnostic
  intermediate metrics.  They must be reported when useful, but they must not
  replace or be presented as final timing QoR.
- A validation report or milestone must not say that a Phase 6 change has
  completed full-flow validation unless both the baseline and candidate have
  successfully completed Phase 7 and their final WNS/TNS have been compared.

## Default timing-QoR terminology is system-global

- Unless a report explicitly qualifies the scope, `WNS` and `TNS` mean the
  whole-original-design timing result after Phase 7, including both paths
  whose endpoints remain on one FPGA and paths that cross one or more FPGAs.
  For cross-FPGA paths, the result must compose routed intra-FPGA logic and
  boundary delay with the concrete Phase 5 slot wait and board-link delay.
  For same-FPGA paths, it must use the corresponding post-route local path
  delay.  Together these two disjoint sets must cover every original
  TimingPathDB path exactly once.
- `global WNS` is the minimum composed slack over all original design paths.
  `global TNS` is the sum of every negative composed path slack, counted once
  per original TimingPathDB path.  A timing-equivalent representative used by
  an optimizer may prove WNS, but it must be expanded to its original members
  before TNS is accumulated.
- WNS/TNS reported by an individual FPGA backend, or an aggregate formed only
  from per-FPGA endpoint reports, must be labelled `per-FPGA physical WNS/TNS`.
  It is a physical diagnostic and must never be presented as the default or
  final global design timing result.
- WNS/TNS formed only from cross-FPGA paths must be labelled
  `cross-FPGA-path-subset WNS/TNS`.  Crossing the board does not by itself make
  that subset a whole-design result.  It must never be labelled `global` unless
  the same-FPGA path population is also included and exact set coverage of the
  complete original TimingPathDB is independently verified.
- Every final global timing claim must report original-path coverage,
  compressed-representative coverage, physical-delay exactness/bound status,
  target-clock and virtual-runtime-clock WNS/TNS, and negative-path counts.
  Missing complete original-member coverage makes final WNS/TNS validation
  incomplete rather than implicitly zero or equal to a per-FPGA aggregate.

## Required Phase 7 A/B comparison

For every Phase 6 QoR claim or default-provider promotion:

1. Use a real synthesizable RTL or gate-level EmuIR design with real logic,
   clocks, and timing constraints.  A contest communication graph, virtual-die
   placement, pin-plan-only bundle, or synthetic connectivity graph is not a
   valid final timing benchmark.
2. Freeze and hash the common upstream EmuIR, Phase 3 assignment, Phase 4
   routes, Phase 5 schedule, BoardDB, architecture/device, constraints, tool
   versions, seed, and relevant physical-flow options.
3. Materialize separate canonical Phase 6 splits for the frozen baseline and
   candidate.  Both splits must pass their normal independent legality,
   electrical-binding, equivalence, and artifact validation gates.
4. Run the complete Phase 7 physical flow for both sides using identical
   backend settings.  Zero unrouted nets, zero DRC violations, complete cell
   accounting, and passed timing-result validation are required before QoR is
   compared.
5. Report at least:
   - explicitly labelled per-FPGA physical WNS and TNS diagnostics;
   - global WNS over all composed original TimingPathDB paths;
   - global TNS as the sum of negative composed slack over all original
     TimingPathDB paths, without representative compression or double
     counting;
   - the absolute baseline-to-candidate change for WNS and TNS, where a
     positive slack delta is an improvement;
   - percentage improvement computed from negative-slack deficit reduction,
     not by dividing signed slack values.  If the baseline is already
     non-negative (WNS) or zero (TNS), report the percentage as N/A.  Report a
     transition across timing closure separately;
   - failing endpoint counts, critical path, runtime, unrouted nets, and DRC
     violations.
6. Preserve the reports and source hashes in a sealed, independently
   replayable bundle.  Intermediate and final results must identify their
   qualification and claim boundary (open academic model, vendor result, or
   hardware closure).

If the selected physical backend does not expose enough timing data to compute
and independently validate TNS, the validation is incomplete.  Implement or
repair TNS extraction and validation before making a final QoR claim; do not
substitute sampled paths, WNS, critical path, or a Phase 6 proxy for TNS.

## Benchmark and execution policy

- Small fixtures are suitable for correctness and determinism tests, but a
  default algorithm or QoR claim also requires a materially sized real design.
- Reuse a frozen Phase 5 or canonical Phase 6 checkpoint when possible; do not
  rerun Phase 1--5 merely to reach Phase 7.  However, never coerce an
  incompatible communication-only artifact into a fake physical netlist.
- Independent A/B runs may and should use different HPC nodes concurrently.
  Each run must use an isolated output directory and the same immutable source
  commit and versioned tool installation.
- A Phase 7 run may be parallelized across FPGAs, provided aggregation remains
  deterministic and the A/B worker configuration is recorded and equivalent.
- A task must remain explicitly pending or blocked until the required Phase 7
  WNS/TNS evidence exists.  Passing Phase 6 alone is not completion.
