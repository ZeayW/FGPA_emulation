# Open-source RTL benchmark catalog

The reproducible source catalog is stored in
`benchmarks/rtl_catalog.json`. Each entry pins an upstream Git commit, license,
top-level module, source paths, approximate scale, and current EmuFlow
readiness. Third-party source is fetched into the ignored
`third_party/rtl/` directory rather than copied into this repository.

List the catalog:

```bash
python3 scripts/benchmarks/fetch.py list
```

Fetch one design:

```bash
python3 scripts/benchmarks/fetch.py fetch picorv32
```

## Recommended progression

The complete gate-by-gate campaign is defined in
`docs/BENCHMARK_VALIDATION_PLAN.md`. The source catalog and fetcher cover the
following progression:

| Level | Design | Approximate upstream scale | Role |
| --- | --- | --- | --- |
| L1 | SERV | about 125 LUT and 164 FF on Artix-7 | Fast real-RTL regression |
| L2 | PicoRV32 | 761-2019 LUT and 442-1085 FF, plus LUTRAM | Main Phase 2 growth target |
| L3 | secworks AES | about 3020 LUT and 2992 FF on Kintex-7 | Medium routing/density and forced partition stress |
| L4 | VTR classic and Ibex | mixed; Ibex is 16.85-66.02 kGE by configuration | Frontend diversity, SystemVerilog and dependency stress |
| L5-L6 | Koios 2.0 | 40 medium and large DL designs | Large RTL, BRAM/DSP and multi-FPGA system stress |
| L7 | NVDLA nvdlav1 | 2048 INT8 MACs plus memories/control | Final very-large hierarchy/runtime stress |

L1 SERV and L2 PicoRV32 have completed the current real-RTL single-FPGA
physical path. See `docs/SERV_L1_VALIDATION.md` and
`docs/PICORV32_L2_VALIDATION.md` for exact metrics and remaining semantic
gates.

The validated PicoRV32 configuration uses its upstream default parameters and
logic-only mapping. It produced 2215 LUT and 1597 FF. Optional
multiply/divide, compressed ISA, IRQ, and barrel-shift features are disabled
by the upstream defaults.

## Current flow gaps exposed by larger designs

The present Phase 2 smoke-test path accepts LUT1-LUT6 and primary FF
placements. Real synthesis of the catalog designs will also exercise:

- CARRY4/CARRY8 conversion;
- LUTRAM and possibly BRAM mapping;
- SRL and wide-mux primitives;
- larger, non-sampled ArchitectureDB regions;
- complete FDCE/FDPE/FDSE control-pin models;
- scalable OpenPARF filler generation and detailed placement;
- fixed clock, IO, memory, DSP, and macro constraints.

For the first PicoRV32 experiment, a logic-only Yosys policy can disable carry,
DSP, BRAM, LUTRAM, SRL, and wide-LUT inference. That provides a large LUT/FF
placement test while the native UltraScale+ packer is implemented. It should
be treated as a placement regression configuration, not as the final QoR
configuration.

Koios is preferred over pre-synthesized placement suites for the large-design
campaign because it supplies editable RTL and explicit soft-logic versus hard
memory/DSP modes. Compile one Koios source file at a time: several variants
reuse top-level module names.
