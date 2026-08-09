# Contest Architecture and Phase 6 Integration Contract

This contract keeps public multi-FPGA contest integration independent from
placement-aware Phase 6 development.  Both tracks consume and emit the common
artifacts below; neither track may reinterpret another phase's decisions.

## Frozen upstream artifacts

- `emuflow.partition-assignment/v1` is the source of instance ownership,
  replication, fixed placement, and directed cut-net endpoints.
- `emuflow.system-routes/v1` is the source of every multicast route and its
  ordered source-to-sink paths.
- `emuflow.system-route-constraints/v1` is the source of shared-capacity,
  maximum-hop, and ratio-domain constraints.
- `emuflow.tdm-schedule/v1` is the source of each schedule-entry identity,
  link, direction, logical lane, slot, round, and frame length. Ratio-plan
  schedules also carry `tdm_ratio`; direct-lane round-barrier schedules omit
  that optional metadata and have the established effective ratio `1`.
- BoardDB is the source of FPGA and link identities.  A contest projection
  remains a virtual academic platform unless a revision-controlled BSP adds
  package pins, electrical rules, clocks, and measured link properties.

Phase 6 may group entries onto physical lanes and add physical annotations. It
must not change a schedule entry's route, direction, effective ratio, logical
lane, slot, round, or target-cycle meaning, and must not rewrite a direct-lane
schedule merely to materialize the implied ratio.

## Frozen Phase 6 boundary

The existing v1 split contracts remain readable throughout the upgrade:

- `emuflow.logical-lane-map/v1`;
- `emuflow.transport-endpoints/v1`;
- `emuflow.virtual-io-anchors/v1`;
- `emuflow.fpga-netlist/v1`;
- `emuflow.split-manifest/v1`; and
- `emuflow.phase6-report/v1`.

New placement, congestion, crossing-signature, grouping, bank, channel, and
package-pin records use new versioned schemas or optional fields.  A new
provider must not silently change the meaning of an existing v1 field.

## Cross-track acceptance

The contest track must prove exact contest-format parsing, official-objective
reconstruction, topology/capacity legality, reproducible input hashes, and
BoardDB projection provenance.  It may stop after Phase 5.

The Phase 6 track must prove exact schedule-entry coverage, zero lane/slot and
physical-resource collisions, independently reconstructed objectives, split
netlist integrity, and cycle equivalence.  Paper-faithful metrics and EmuFlow
board-aware extensions are reported separately.

The combined gate runs a frozen contest-derived BoardDB and Phase 5 schedule
through the upgraded Phase 6 and verifies that all frozen upstream fields are
byte-for-byte unchanged after path normalization.  Results on a contest
projection are academic architecture validation, not hardware sign-off.
