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

| Order | Design | Approximate upstream scale | Role |
| --- | --- | --- | --- |
| 1 | SERV | about 125 LUT and 164 FF on Artix-7 | Fast multi-hundred-cell regression |
| 2 | PicoRV32 | 761-2019 LUT and 442-1085 FF, plus LUTRAM | Main Phase 2 growth target |
| 3 | secworks AES | about 3020 LUT and 2992 FF on Kintex-7 | Medium routing/density stress |
| 4 | Ibex | 16.85-66.02 kGE depending on configuration | SystemVerilog and dependency stress |
| 5 | VTR classic/Koios | mixed | CAD research and later large-scale tests |

PicoRV32 is the preferred next integration because its CPU RTL is contained in
one Verilog file, it has an ISC license, and upstream reports results for the
same `xcvu3p-ffvc1517-2-e` target family. The first configuration should
disable optional multiply/divide, compressed ISA, IRQ, and wide barrel-shift
features.

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
