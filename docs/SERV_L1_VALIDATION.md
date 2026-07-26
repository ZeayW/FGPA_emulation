# SERV L1 validation

## Result

SERV is the first real open RTL benchmark to complete the current single-FPGA
physical path on `proj169-2`:

```text
pinned SERV RTL
  -> Yosys xcup logic-only synthesis
  -> flattened mapped JSON and Verilog
  -> EmuIR
  -> OpenPARF Bookshelf
  -> OpenPARF global placement and legalization
  -> EmuFlow name restoration and legality checks
  -> Vivado intra-SLICE FF BEL repair
  -> Vivado routing, DRC, and timing
  -> routed DCP
```

Validation date: 2026-07-26.

## Reproducible inputs

- SERV revision: `41e8aeedfd1e9ad5f95902c5b0dfc83d1c99e5d2`
- top: `serv_synth_wrapper`
- target: `xcvu3p-ffvc1517-2-e`
- Yosys: `0.33+103`
- OpenPARF: project-local CPU build
- Vivado: `2025.2`
- virtual timing constraint: `clk`, 10.0 ns
- synthesis policy: `logic-only`

The wrapper exposes SERV's register-file interface instead of inferring the
register-file RAM. The logic-only policy disables carry, wide mux, DSP, BRAM,
LUTRAM, and SRL mapping. It is intended to validate the flow boundary, not
native UltraScale+ QoR.

Run the complete remote validation:

```bash
scripts/remote/proj169-2.sh serv-l1-all
```

When the project, ArchitectureDB, OpenPARF, and SERV source are already
synchronized:

```bash
scripts/remote/proj169-2.sh serv-l1
```

## Observed metrics

| Boundary | Result |
| --- | --- |
| RTL source files | 18, each recorded with SHA-256 |
| mapped primitives | 436 |
| LUT | 255 |
| FF | 181 |
| EmuIR nets | 507 total cut-classified nets |
| OpenPARF emitted nets | 427 |
| OpenPARF placement | 436/436 cells legal |
| SLICE sites used | 48 of the sampled 64 |
| Vivado routable nets | 414 |
| fully routed nets | 414 |
| routing errors | 0 |
| DRC checks found | 0 |
| 100 MHz setup WNS/TNS | 5.546 ns / 0.000 ns |
| hold WHS/THS | 0.054 ns / 0.000 ns |

The routed checkpoint is generated at:

```text
build/remote/benchmarks/serv-l1/vivado/routed.dcp
```

## Issues exposed and fixed

The small eight-cell fixture did not expose these integration defects:

1. Yosys retained mapped hierarchy, so the importer initially saw two module
   instances instead of 474 mapped cells. The synthesis boundary now performs
   an explicit post-mapping `flatten`.
2. Logic-only Yosys retained six `INV` helpers. They are now normalized to
   functionally equivalent `LUT1` cells.
3. OpenPARF Bookshelf rejected Yosys names containing `$`, backslashes,
   brackets, colons, and source paths. The adapter now emits stable `iN`/`nN`
   identifiers and a reversible `name_map.json`.
4. OpenPARF's LUT demand operator rejects LUT1. LUT1 is modeled with LUT2 area
   demand during placement while its physical type and INIT remain LUT1.
5. Vivado exposes embedded backslashes in mapped Yosys names as doubled
   characters. XDC queries now use bytewise hexadecimal regular expressions.
6. Exact FF BEL assignments can violate UltraScale+ CE/control-set sharing.
   OpenPARF's SLICE/LOC remains fixed; Vivado repairs only the FF BEL inside
   that SLICE. LUT Site/BEL assignments remain fixed.

## Remaining semantic work

This result closes the real-RTL physical loop, but it is not yet the
multi-FPGA complete flow:

- RTL-to-mapped sequential equivalence is not yet an automated gate;
- constant pins and primary terminals need explicit Bookshelf terminal models;
- native CARRY8/LUTRAM packing is not enabled;
- partitioning is now implemented and validated on PicoRV32; system routing,
  TDM, transport RTL, and lane assignment remain later phases and are not
  exercised by this single-FPGA L1 run.

PicoRV32 has now completed both the larger L2 logic-only physical regression
and the Phase 3 G4 partition regression; see
`docs/PICORV32_L2_VALIDATION.md` and `docs/PHASE3_VALIDATION.md`.
