# EmuFlow validation requirements

These requirements apply to all work in this repository.

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
   - per-FPGA WNS and TNS;
   - overall WNS, defined as the minimum WNS across all implemented FPGAs and
     timing domains;
   - overall TNS, defined as the sum of all negative endpoint slacks across all
     implemented FPGAs and timing domains, without double counting;
   - the absolute and percentage baseline-to-candidate change for WNS and TNS;
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
