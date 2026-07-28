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
| L5 scale gate | PicoRV32 x32 harness | 121,984 mapped LUT/FF primitives | 100k-cell frontend, OpenPARF and routing scalability |
| L5-L6 | Koios 2.0 | 40 medium and large DL designs | Large RTL, BRAM/DSP and multi-FPGA system stress |
| L7 | NVDLA nvdlav1 | 3,123,117 synthesized cells; 1,825,473 LUTs and 915,739 FFs | Real connected million-cell stress |

L1 SERV, L2 PicoRV32, and the 121,984-cell PicoRV32 x32 scale harness have
completed the current real-RTL single-FPGA physical path. See
`docs/SERV_L1_VALIDATION.md`, `docs/PICORV32_L2_VALIDATION.md`, and
`docs/PICORV32_X32_100K_VALIDATION.md` for exact metrics and remaining
semantic gates.

The connected VeeR EH1 baseline synthesized to 48,095 EmuIR cells. The
connected NVDLA `NV_nvdla` top then passed a substantially larger Vivado
frontend gate with 3,123,117 hierarchical cells. Its 1.1 GB structural
Verilog exhausted the practical memory budget of the current Yosys JSON
bridge, which was stopped at 118 GB RSS before it could emit EmuIR. See
`docs/VEER_EH1_VALIDATION.md` and
`docs/NVDLA_NVDLAV1_VALIDATION.md`. This is now the primary importer
scalability milestone; NVDLA is not recorded as a completed end-to-end run.

The bounded but still genuine `NV_NVDLA_partition_a` CACC top avoids that
whole-chip importer ceiling while retaining a connected 731,313-cell mapped
design. It has passed the logical path through TritonPart, system routing,
TDM, virtual pin planning, per-FPGA split, and mapped equivalence on a
four-VU9P virtual platform. It also completes four per-FPGA OpenPARF/Vivado
implementations and the independent Phase 7C gate: 731,387 mapped cells plus
one recorded BUFGCE, zero unrouted nets, zero DRC violations, and +1.435 ns
worst WNS. Its physical closure experiment and the important atomic-cluster
imbalance are recorded in
`docs/NVDLA_PARTITION_A_FULL_FLOW.md`.

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

Koios remains useful for intermediate BRAM/DSP coverage, but the official
NVDLA top is now the final scale target. Compile one Koios source file at a
time: several variants reuse top-level module names. The first logic-only DLA
attempts exceeded 64-79 GB memory because soft memories expanded into logic,
so Koios physical validation is deferred until native BRAM/DSP preservation
is available.
