# Phase 8A hardware-BSP readiness validation

## Scope and status

Phase 8A formalizes the boundary between the completed board-independent
G0-G9 flow and board-specific G10. It is intentionally runnable before a
physical board is selected.

The increment is complete. It generates and independently reconstructs a
versioned `emuflow.bsp-requirements/v1` artifact from:

- a passing, hashed Phase 7D release manifest;
- the independently checked Phase 6 report and per-FPGA virtual anchors;
- the virtual BoardDB topology, device parts, lane widths, link directions,
  link modes, clocks, and latency.

The Phase 8A report has `status: pass` when this requirements contract is
complete and internally consistent. It separately records
`board_binding_status: awaiting_hardware_bsp` and `g10_status: not_run`.
It never treats requirements generation as hardware closure.

## Contract algorithm

The validator performs the following checks and expansions:

1. Require an exact 40-hex source commit, the SHA-256 of the Phase 7D
   manifest, and passing evidence for exactly G0 through G9.
2. Require Phase 6 and Phase 7D to agree on design, platform, and the virtual
   board-binding boundary.
3. Reload every per-FPGA virtual anchor and check its FPGA part, BoardDB link,
   peer, direction, and lane index. The total must equal both Phase 6's
   `virtual_anchors` and `unbound_package_pins`.
4. Expand BoardDB links into physical data-lane endpoints. A full-duplex
   `N`-lane link requires `2 directions x 2 endpoint pins x N` bindings; a
   unidirectional link requires `2 x N`, and a half-duplex link requires one
   shared inout endpoint per FPGA and lane.
5. Generate one fabric-clock contract per FPGA and one timing/electrical/
   training contract per directed link channel.
6. Require one routed-DCP artifact for every FPGA and create a part-specific
   bitstream slot without claiming that a bitstream already exists.
7. Emit explicit pending checks for BSP electrical binding, board IO timing
   and DRC, bitstream generation, link PRBS/training/deskew, and a golden
   hardware workload.
8. Reload the written artifact, rebuild it independently, and require exact
   equality. The remote runner executes the whole operation twice and
   byte-compares both requirements and report files.

Invalid BoardDB lanes, incomplete FPGA anchor coverage, anchor-count
disagreement, incomplete G0-G9 evidence, non-virtual input releases, and
missing routed checkpoints are fatal validation errors.

## Unit validation

The repository has 96 passing unit tests after this increment. Phase 8A adds
coverage for:

- exact two-FPGA full-duplex lane expansion;
- rejection of an anchor outside the BoardDB lane range;
- rejection of a missing per-FPGA routed checkpoint;
- byte-reproducible requirements and report generation.

## Real 731k-cell NVDLA result

The balanced NVDLA `NV_NVDLA_partition_a` result on `proj169-2` is the
acceptance design. Phase 8A consumes the Phase 7D manifest with SHA-256
`1bd5f3e3681dec8018a78076489c899e818376cf705c0dd21a7ee57d85a6ba49`
and source commit
`63b05710466d35a64759ae51a1c51772e957c7ab`.

| Metric | Result |
| --- | ---: |
| target FPGAs / part | 4 / `xcvu9p-flga2104-2L-e` |
| BoardDB links | 4 full-duplex, source-synchronous |
| logical anchors checked | 512 |
| physical data-lane endpoints generated | 512 |
| fabric-clock bindings | 4 |
| directed link-channel bindings | 8 |
| bitstream slots | 4 |
| pending G10 checks | 5 |
| double-run wall time / peak RSS | 0.68 s / 84,188 KiB |

The accepted output is:

```text
EMUFLOW_NVDLA_PHASE8A status=pass \
board_binding=awaiting_hardware_bsp g10_status=not_run \
logical_anchors=512 physical_data_lane_endpoints=512 \
fabric_clock_bindings=4 link_channel_bindings=8 bitstreams=4 \
requirements_sha256=8d9d745ba5ac5b1b5495db8e335ad830ccb7404a34263eef2cb05319ec1f02b8
```

Both 316,776-byte requirements files have the SHA-256 above. Both Phase 8A
reports have SHA-256
`d198abc6bc34fd14c6addb2c10d8503e8c5be9b41b5954b01fa6932bc8d164cb`.

## Reproduction

The NVDLA wrapper executes two independent runs and compares them:

```bash
scripts/remote/nvdla_partition_a_balanced.sh phase8a ROOT
```

The generic CLI is:

```bash
PYTHONPATH=src python3 -m emuflow phase8a \
  --release-manifest ROOT/phase7d/release_manifest.json \
  --phase6-report ROOT/phase6/phase6_report.json \
  --platform platforms/virtual/xcvu9p_4fpga_mesh.json \
  --anchor fpga0=ROOT/phase6/fpga0/virtual_anchors.json \
  --anchor fpga1=ROOT/phase6/fpga1/virtual_anchors.json \
  --anchor fpga2=ROOT/phase6/fpga2/virtual_anchors.json \
  --anchor fpga3=ROOT/phase6/fpga3/virtual_anchors.json \
  --out ROOT/phase8a
```

## Remaining hardware boundary

Selecting a board is now a data-binding step with an explicit input contract,
not an implicit redesign of the earlier flow. Phase 8B will consume a
hardware BoardDB/BSP and must validate real package pins, banks, IOSTANDARDs,
clock-capable pins and buffers, source-synchronous timing, and connector lane
maps before Vivado bitstream generation. G10d and G10e additionally require
access to the physical board.
