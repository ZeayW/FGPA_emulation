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

## Universal experiment lifecycle and checkpoint reuse

These rules apply to every present and future EmuFlow experiment: correctness
and determinism validation, benchmark qualification, algorithm A/B and
ablation studies, scalability and performance measurements, public-contest
evaluation, synthesis and partitioning runs, routing and scheduling studies,
Phase 6 provider comparisons, physical implementation, timing closure, and
complete Phase 1--7 flows.  Phase 6 is only one application of this policy.

- Before starting any repeated, multistage, expensive, or evidence-producing
  run, express it as a content-addressed DAG through `experiment-cache`.  A
  cheap one-off diagnostic may run outside the DAG only when it will not be
  used as benchmark, qualification, completion, performance, or QoR evidence.
- Decompose the DAG at real reusable boundaries.  Phase 1--5 is not a universal
  cache node: frontend/synthesis, timing preparation, partitioning, system
  routing, and TDM scheduling are separate nodes whenever their implementations
  or inputs can change independently.  Each node must declare exact dependency
  keys, input SHA-256 values, configuration, command/environment contract,
  seed/worker count where relevant, expected artifacts, a measured peak and
  retained-byte estimate, and an independent semantic validator.
- A Git commit is provenance, not cache identity.  Every v2 node must carry a
  portable implementation closure containing the exact source, script, and
  binary files used by execution, and a separate closure for its validator.
  An implementation change invalidates that node and descendants; a validator-
  only change triggers independent revalidation without recomputing output.
- The DAG implementation and experiment spec must support arbitrary named
  stages and multiple dependencies; it must not hard-code one current flow's
  phase sequence.  Physical lookahead, source preparation, qualification, and
  aggregation are explicit reusable nodes whenever they are shared or have a
  different invalidation boundary.
- Plan before execution.  Inventory existing caches and repository-external
  archives, independently validate compatible prior artifacts, and import
  valid results before submitting work.  A new branch, report, experiment
  label, comparison arm, directory layout, or downstream objective is never by
  itself a reason to recompute an unchanged node.
- Execute only the smallest missing frontier.  A changed input, option, tool,
  or dependency invalidates that node and its descendants, not unrelated
  nodes or valid ancestors.  Never delete or bypass a whole cache as a shortcut
  for targeted invalidation, and never restart completed ancestors merely to
  recover from or resume a downstream failure.
- Cache reuse is authorized only by the complete content identity plus a
  passing independent validator and sealed artifacts.  Names, paths, mtimes,
  logs, a declared `pass` status, or visually similar results are insufficient.
  If a stage has no adequate validator, its result is not reusable evidence
  until that validation gap is repaired.
- Fair A/B and ablation experiments must share the exact validated upstream
  checkpoints and differ only in the intended variable.  Compute each unique
  baseline once.  Reuse a valid baseline in every later comparison rather than
  rerunning it for symmetry or presentation.
- Preserve failed and partial run evidence outside the repository, then
  re-plan from the last valid checkpoint.  Do not overwrite another attempt's
  artifacts, silently turn a failed attempt into a fresh run directory, or
  publish a partial checkpoint as complete.
- Keep three storage classes physically separate: immutable content-addressed
  checkpoints, append-only per-execution attempts, and self-contained final
  evidence bundles.  Each retry gets a new `attempt-NNNN` directory.  Logs,
  scratch, and failed partial output never become checkpoint artifacts merely
  because they share a parent directory.
- Every declared artifact has a semantic role.  `consumer-checkpoint`,
  `source-input`, and `evidence-critical` are required retention;
  `diagnostic` and `failure-diagnostic` are optional evidence; only
  `regenerable-scratch` is prunable.  File size alone never decides retention.
  In particular, 64 MiB is not a replay limit.
- HPC farms may submit only `ready` cache misses.  Re-plan after every completed
  frontier; skip `reuse` nodes and keep `waiting` nodes blocked on their exact
  dependency keys.  Concurrent tasks require isolated output directories and
  immutable source/tool identities.  Parallelism changes scheduling, not the
  evidence contract, unless the worker count is explicitly part of identity.
  If the whole ready frontier exceeds quota or desired concurrency, use the
  farm compiler's explicit experiment-node subset; never edit a plan or task
  command by hand. Deferred ready nodes retain the same identity for a later
  batch.
- Farm workers use leases and heartbeats.  A silent or expired task is not
  automatically dead: reconciliation must probe its recorded PID on its pinned
  node.  Only an expired lease plus a confirmed-absent process can become
  `retryable`, and the retry must use a new attempt directory.
- A cached checkpoint proves only that one node completed its declared gate.
  It does not by itself prove an end-to-end claim.  Report completion or QoR
  only when every required terminal node and claim-specific validator exists;
  otherwise retain an explicit planned, running, incomplete, or blocked state.
- Experiment specs, plans, farm state, logs, transient paths, and large
  artifacts stay outside the source repository.  Check in only reusable
  schemas, policies, small lawful fixtures, and canonical benchmark registries.
- Force-rerunning an otherwise valid checkpoint is exceptional.  Record the
  explicit reason (for example, nondeterminism replication or measurement
  noise study) and give the repeated run a distinct declared identity; never
  make force-rerun the default behavior of an automation or validation task.
- Cache reclamation is mark-and-sweep, never age/name-based deletion.  Root all
  active v2 plans and explicit pins, inventory and validate objects, then
  generate a sealed GC plan.  Apply it only by the exact approved plan SHA-256;
  abort if any candidate changed or became referenced.  Legacy runs first get
  a read-only migration inventory, then independent validation/import or an
  explicit diagnostic-retention decision.  When a noncanonical legacy case is
  deliberately retired, use `retirement-plan` followed by `retirement-apply`:
  the apply step requires the exact plan SHA-256, rehashes every selected tree
  before any mutation, refuses evidence/archive candidates, and preserves
  marker tombstones plus a non-evidence receipt outside `runs`.  Direct
  age/name/glob-based deletion remains forbidden.
- A final evidence bundle recursively contains every required artifact for its
  terminal nodes and ancestors and must validate after the source cache is
  unavailable.  A legacy archive containing any hash-only run file is not
  replay-complete and must never authorize deletion of its source run.

## Mandatory experiment storage boundary

- Every EmuFlow-controlled writable path on the validation servers must be
  located below `/research/d4/gds/ziyiwang21`.  This includes run directories,
  content-addressed caches, staging areas, temporary directories, farm state,
  logs, build scratch, extracted inputs, checkpoints, archives, reports, and
  tool-generated physical work directories.
- Do not use node-local or alternate storage for EmuFlow work.  In particular,
  `/dev/shm`, `/tmp`, `/var/tmp`, `/uac`, home-directory scratch, and any path
  outside `/research/d4/gds/ziyiwang21` are prohibited for experiment outputs
  or temporary artifacts, even as a quota workaround or performance
  optimization.  Set `TMPDIR` and tool-specific scratch variables to a unique
  directory under the required `/research` root when needed.
- Never silently fall back to another filesystem.  Before launching an
  expensive DAG frontier, check the user quota and estimate the frontier's
  peak retained plus temporary footprint.  If the available quota is
  insufficient, keep the frontier blocked, report the storage requirement,
  and reclaim space only through the validated archive/retention process.
- Storage cleanup must remain evidence-aware.  Preserve sealed final reports,
  manifests, hashes, placement/route artifacts required for replay, and the
  minimal valid checkpoints.  Classify and obtain an explicit safe cleanup
  set before removing regenerable physical scratch, duplicated ancestors,
  obsolete failed staging, or redundant captures; never delete unrelated
  tasks or unvalidated evidence merely to make a run fit.

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

- `benchmarks/end_to_end_validation_matrix.json` is the sole registry for
  provider comparisons and complete Phase 1--7 WNS/TNS claims.  Ad-hoc runs
  may diagnose a bug, but they must not be reported as benchmark evidence.
- Every registered full-flow case has two independently named axes:
  `workload` is a naturally connected, hash-pinned upstream RTL design and
  `platform` is a hash-pinned public-contest case materialized as BoardDB.
  The workload supplies cells/nets and the contest case supplies only FPGA
  topology and link capacities.  Never feed contest communication nodes to
  synthesis or describe a raw contest-graph result as a physical RTL run.
- Always identify a run by the canonical `<workload>__<suite>-<case>` ID.  A
  bare label such as `case6`, `case07`, or `NVDLA run` is ambiguous and is not
  acceptable in reports, manifests, filenames, or user-facing summaries.
- The canonical initial QoR set is Koios DLA medium combined separately with
  EDA 2023 case6, case7, and case9 BoardDBs.  Case6 is the primary QoR case;
  case7 and case9 are topology replications.  Adding or replacing a case
  requires updating the versioned matrix and its validator tests first.
- The raw public-contest coverage plan remains separately recorded in
  `benchmarks/contest_validation_matrix.json`.  Passing fetch/import/evaluate
  for that matrix proves a communication-algorithm gate only; it never
  promotes an entry in the end-to-end matrix.
- A matrix entry in `planned` or `blocked` state is not evidence.  `qualified`
  requires a content-addressed replayable manifest for all required providers,
  physical seeds, gates, hashes, global timing metrics, DRC, and unrouted-net
  checks.  Repository configuration must never contain transient server paths.
- Baseline, placement-aware, and Chimew Phase 6 arms must use identical frozen
  source, BoardDB, Phase 1/3/4/5 artifacts, physical backend/options, worker
  count, and seeds 1/2/3.  Only the Phase 6 provider may differ.
- The primary final QoR is whole-design target-clock WNS and TNS after Phase
  7/7C.  Per-FPGA WNS/TNS, Phase 6 cost, crossings, RUDY, and congestion are
  diagnostics and must not be substituted for the primary metrics.
- Small fixtures are suitable for correctness and determinism tests, but a
  default algorithm or QoR claim also requires a materially sized real design.
- Replicated-core or artificially coupled RTL harnesses are not accepted as
  benchmark-catalog entries or as evidence for provider promotion and final
  WNS/TNS claims. Use a naturally connected upstream RTL design for those
  decisions.
- Applying the universal experiment policy to a provider comparison means a
  reusable Phase 1→timing→Phase 3→Phase 4→Phase 5 chain, one Phase 6 checkpoint
  per provider, and Phase 7 checkpoints keyed by provider and physical seed.
  Baseline Phase 6 consumes Phase 5 directly.  The fixed physical lookahead
  consumes baseline Phase 6; placement-aware and Chimew consume Phase 5 plus
  that lookahead.  Reuse every valid ancestor; do not rerun Phase 1--5 merely
  to reach Phase 7.  Never coerce an incompatible communication-only artifact
  into a fake physical netlist.
- Use the checked-in `experiment-stage` run/validate pairs for every canonical
  boundary: `frontend`, `timing`, `partition`, `cut-timing`, `route`, `tdm`,
  the hard-linked `shared` view, physical lookahead, Phase 6, and Phase 7.
  Generate the provider/seed DAG with `benchmark-experiment-compile`; do not
  hand-collapse Phase 1--5 into a monolithic command. The compiler must bind
  its case to the checked-in end-to-end matrix and must verify the run-spec
  RTL/top/clocks plus the contest BoardDB and route-constraints materialization
  report. Route/hop/TDM limits from that report must feed and be independently
  checked at Phase 3, Phase 4, and Phase 5; an arbitrary platform or default
  ratio quantum is not acceptable. Materialize physical
  lookahead once at a declared
  seed, derive both placement-aware and Chimew inputs from that same frozen
  placement, and reuse the lookahead itself as baseline Phase 7 at the matching
  seed.  Do not hide a fresh baseline physical run inside either candidate arm.
- Existing pre-cache results should be registered with `experiment-cache
  import` only after the node's independent semantic validator passes and all
  declared artifacts pass hash sealing.  New node executions use the same
  validator before cache publication.
  Imported artifacts remain externally stored and any later byte change must
  break reuse.  Do not rerun a valid baseline merely to make its directory
  layout resemble a newer candidate.
- Re-plan after every completed DAG frontier.  Only `ready` cache misses may be
  compiled into a validation farm; `reuse` nodes are skipped and `waiting`
  nodes remain blocked on their exact dependency keys.  A changed Phase 6
  option invalidates that provider and its Phase 7 descendants, while a changed
  RTL or BoardDB hash invalidates the shared checkpoint and every descendant.
- Independent A/B runs may and should use different HPC nodes concurrently.
  Each run must use an isolated output directory and the same immutable source
  commit and versioned tool installation.
- A Phase 7 run may be parallelized across FPGAs, provided aggregation remains
  deterministic and the A/B worker configuration is recorded and equivalent.
- A task must remain explicitly pending or blocked until the required Phase 7
  WNS/TNS evidence exists.  Passing Phase 6 alone is not completion.
