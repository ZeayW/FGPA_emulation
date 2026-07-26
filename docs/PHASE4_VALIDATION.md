# Phase 4 board-level system routing validation

## Result

Phase 4 is implemented and passed G5 on `proj169-2` using the 140 real
register-output cut nets from the connected PicoRV32 Phase 3 assignment:

```text
Phase 3 cut nets
  -> directed BoardDB graph
  -> deterministic multicast shortest-path trees
  -> negotiated congestion and historical link cost
  -> per-direction frame-capacity accounting
  -> independent reachability/cycle/capacity reconstruction
```

Validation date: 2026-07-27.

All 140 demands and all 140 remote FPGA sinks were routed over the virtual
two-FPGA link. There are no unreachable sinks, cycles, illegal directions, or
overloaded link directions.

## Implemented artifacts and commands

Phase 4 adds:

- `emuflow.system-route-constraints/v1`: frame-slot budget, unavailable links,
  and negotiated-routing iteration limit;
- `emuflow.system-routes/v1`: normalized demands, unicast/multicast trees,
  directed link utilization, latency, and recomputable metrics;
- `emuflow.phase4-report/v1`: pipeline and independent checker result.

The CLI entry points are:

```bash
PYTHONPATH=src python3 -m emuflow phase4 \
  --assignment build/phase3/assignment.json \
  --platform platforms/virtual/xcvu3p_2fpga_p2p.json \
  --out build/phase4 \
  --frame-slots 32

PYTHONPATH=src python3 -m emuflow route validate \
  build/phase4/routes.json \
  --assignment build/phase3/assignment.json \
  --platform platforms/virtual/xcvu3p_2fpga_p2p.json
```

The remote regression is:

```bash
scripts/remote/proj169-2.sh picorv32-phase4
```

## Capacity model

Phase 4 uses a board-routing capacity budget measured in signal bits per
virtual frame:

```text
directed capacity bits = physical data lanes x allowed frame slots
```

The validated virtual link has 32 lanes per direction and a 32-slot planning
budget, giving 1,024 bits per direction per frame. Full-duplex links receive
independent directional budgets. Half-duplex links share one budget across
both directions. An ordered unidirectional BoardDB link permits only
`endpoints[0] -> endpoints[1]`.

This is a planning bound, not yet a generated schedule. Phase 5 must assign
each bit-hop to a concrete lane/slot, account for multi-hop precedence and
link latency, and derive the achievable virtual DUT clock.

## Routing algorithm

The dependency-free provider:

1. converts every Phase 3 cut net into one source and one or more remote FPGA
   sinks;
2. builds legal directed arcs from BoardDB link direction;
3. creates a shortest-path predecessor tree from each source;
4. unions sink paths into an acyclic multicast tree;
5. charges a net once per shared tree edge;
6. increases present and historical cost on congested capacity domains;
7. reroutes all demands until capacity is legal or reports infeasibility.

The independent checker reconstructs demands from the Phase 3 assignment and
rebuilds the BoardDB graph. It checks exact demand coverage, legal directed
edges, source reachability, every sink, tree acyclicity, unique predecessor
paths, route latency, full/half-duplex capacity domains, utilization records,
and summary metrics.

## Connected PicoRV32 result

| Metric | Result |
| --- | ---: |
| Phase 3 cut demands | 140 |
| routed remote sinks | 140 |
| multicast tree edges | 140 |
| routing iterations | 1 |
| path latency | 2 fabric cycles for all 140 demands |
| total bit-hops | 140 |
| overloaded links | 0 |
| runtime | 0.10 s |
| peak RSS | 18,972 KiB |
| routes artifact | 67,606 bytes |
| complete output directory | 80 KB |
| routes SHA-256 | `5c18a0adb18909c90ac48b37b707d39b1623c00d14cbc0e62b94317fde229ac6` |

Directional utilization:

| Capacity domain | Used | Capacity | Utilization |
| --- | ---: | ---: | ---: |
| `fpga0 -> fpga1` | 9 bits/frame | 1,024 | 0.879% |
| `fpga1 -> fpga0` | 131 bits/frame | 1,024 | 12.793% |

The route was generated twice from the same assignment and constraints. Both
complete `routes.json` files had the same SHA-256. A separate CLI invocation
then reloaded and independently validated the first artifact.

## Additional topology coverage

The test suite includes:

- a four-FPGA diamond where two unit-capacity demands must use both paths;
- multicast routing to three sinks with a shared acyclic tree;
- unavailable-link reachability failure;
- infeasible single-link congestion;
- half-duplex capacity shared across opposing directions;
- injection of a malicious route cycle;
- a real counter partition passed through the complete Phase 3-to-Phase 4
  local pipeline.

The complete local and remote suite now has 42 passing tests.

## G5 acceptance

| Requirement | Evidence |
| --- | --- |
| every cut net reaches every sink | 140/140 demands and 140/140 sinks |
| no modeled link exceeds capacity | 12.793% maximum utilization |
| route trees contain no cycles | independently reconstructed predecessor trees |
| utilization is independently recomputed | exact directional bit counts match |
| infeasibility is actionable | unavailable and over-capacity tests fail with diagnostics |
| fixed inputs are reproducible | byte-identical route artifacts and SHA-256 |

## Remaining limitations and Phase 5 handoff

The current provider is a deterministic negotiated shortest-path baseline. It
does not yet optimize timing criticality, reserve shell traffic, split demand
classes, or use an external ILP/min-cost-flow provider. Real-design validation
uses the present two-node topology; multi-hop and multicast behavior is
covered by independent topology tests.

Phase 5 now consumes `routes.json`, schedules all 140 bit-hops, and passes
independent collision, precedence, latency, completion, Python transport, and
compiled RTL transport checks. See `docs/PHASE5_VALIDATION.md`.
