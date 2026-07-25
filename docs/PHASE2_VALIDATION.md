# Phase 2 validation record

Validation date: 2026-07-25

Target:

- SSH alias: `proj169-2`
- execution host: `proj169`
- part: `xcvu3p-ffvc1517-2-e`
- Vivado: 2025.2
- OpenPARF source: `793cac2bb109e5cc76046f87d39ed70fa093cf60`
- OpenPARF mode: CPU, Python 3.8/PyTorch 1.8 `deepgate` environment

## Test design

The physical smoke test contains four LUT2 and four FDRE primitives. It uses a
locked mapped fixture so Phase 2 tests placement rather than depending on the
future CARRY4-to-CARRY8 site-packing pass.

Vivado exported a compact 8-by-8 device window:

- 32 SLICEL sites;
- 32 SLICEM sites;
- 512 conservative LUT slots;
- 512 conservative primary-FF slots.

## Validated path

```text
mapped Yosys JSON
  -> EmuIR
  -> OpenPARF Bookshelf
  -> OpenPARF global placement
  -> OpenPARF UltraScale slot legalization
  -> result.pl
  -> EmuFlow Site/BEL legality checker
  -> LOC/BEL XDC
  -> Vivado placement completion
  -> Vivado routing
  -> routed DCP
```

The OpenPARF result used two SLICE sites for all eight cells. EmuFlow reported:

```text
provider=openparf status=pass cells=8 sites_used=2 placement=legal
```

Vivado preserved all eight LOC/BEL constraints and reported:

```text
routable nets=10 fully routed nets=10 nets with routing errors=0
```

The routed checkpoint is generated remotely at:

```text
/home/ziyiwang21/work/FGPA_emulation/build/remote/phase2/vivado-openparf/routed.dcp
```

## Reproduction

After the one-time OpenPARF source upload and build:

```bash
scripts/remote/proj169-2.sh openparf-sync
scripts/remote/proj169-2.sh openparf-build
```

run:

```bash
scripts/remote/proj169-2.sh sync
scripts/remote/proj169-2.sh phase2-arch
scripts/remote/proj169-2.sh phase2
scripts/remote/proj169-2.sh openparf-run
scripts/remote/proj169-2.sh phase2-vivado-openparf
```

## Scope boundary

This validates the executable Phase 2 adapter and physical-placement risk
spike. It does not yet satisfy the full planned
`FPGA Interchange -> OpenPARF -> FPGA Interchange -> RapidWright DCP` loop.
Fixed IO/clock macros, CARRY8/DSP/BRAM packing, detailed pin permutation,
intra-site routing repair, and RapidWright conversion remain follow-on work.

OpenPARF's optional ISM detailed-placement pass is disabled only for this
eight-cell regression because the upstream implementation raises a
small-design allocation error. Global placement, UltraScale slot
legalization, EmuFlow's independent legality checks, and Vivado routing all
run in the validated path.
