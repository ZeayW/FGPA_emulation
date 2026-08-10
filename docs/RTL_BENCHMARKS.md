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

The checked-in run contracts cover SERV L1, PicoRV32 L2, secworks AES L3,
the PicoRV32 x32 L5 scale harness, and the current Koios L5 fixtures. Run the
AES progression rung with:

```bash
python3 scripts/benchmarks/fetch.py fetch secworks_aes
emuflow benchmark benchmarks/runs/secworks_aes_l3.json \
  --source-root third_party/rtl/secworks_aes \
  --out build/secworks-aes-l3
```

Its `logic-only` policy is an open-flow integration baseline, not a claim that
its mapped QoR matches the upstream Kintex-7 result.

## Recommended progression

The source catalog and fetcher cover the following progression:

| Level | Design | Approximate upstream scale | Role |
| --- | --- | --- | --- |
| L1 | SERV | about 125 LUT and 164 FF on Artix-7 | Fast real-RTL regression |
| L2 | PicoRV32 | 761-2019 LUT and 442-1085 FF, plus LUTRAM | Main Phase 2 growth target |
| L3 | secworks AES | about 3020 LUT and 2992 FF on Kintex-7 | Medium routing/density and forced partition stress |
| L4 | VTR classic and Ibex | mixed; Ibex is 16.85-66.02 kGE by configuration | Frontend diversity, SystemVerilog and dependency stress |
| L5 scale gate | PicoRV32 x32 harness | 121,984 mapped LUT/FF primitives | 100k-cell frontend, OpenPARF and routing scalability |
| L5-L6 | Koios 2.0 | 40 medium and large DL designs | Large RTL, BRAM/DSP and multi-FPGA system stress |
| L7 | NVDLA nvdlav1 | 3,123,117 synthesized cells; 1,825,473 LUTs and 915,739 FFs | Real connected million-cell stress |

The progression is an acceptance ladder, not a checked-in result table.
Machine configuration, exact synthesis counts, QoR measurements, logs, and
artifact hashes are maintained in local experiment records.

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

For an initial PicoRV32 run, a logic-only Yosys policy can disable carry,
DSP, BRAM, LUTRAM, SRL, and wide-LUT inference. That provides a large LUT/FF
placement test while the native UltraScale+ packer is implemented. It should
be treated as a placement regression configuration, not as the final QoR
configuration.

Koios remains useful for intermediate BRAM/DSP coverage, while the official
NVDLA top is the final scale target. Compile one Koios source file at a time:
several variants reuse top-level module names. Native BRAM/DSP preservation is
required before interpreting logic-only Koios results as representative QoR.
