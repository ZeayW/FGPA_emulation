# PicoRV32 x32 100k-cell validation

## Result

A 32-core PicoRV32 stress harness completed the current single-FPGA physical
path on `proj169-2`:

```text
pinned open PicoRV32 RTL plus x32 harness
  -> Yosys xcup logic-only synthesis
  -> 121,984-cell EmuIR
  -> OpenPARF global placement and legalization
  -> exact name restoration and Site/BEL legality checks
  -> Vivado control-set spill repair
  -> Vivado placement, routing, DRC, and timing
  -> fully routed DCP
```

Validation date: 2026-07-26.

This is the first EmuFlow run above the requested 100,000 mapped-cell
threshold. It passes G0-G4, G8, and the routability/DRC portion of G9. The
100 MHz setup constraint does not close, and G5-G7 are not yet implemented.

## Reproducible inputs

- PicoRV32 revision:
  `87c89acc18994c8cf9a2311e871818e87d304568`
- PicoRV32 source SHA-256:
  `0836050971b3c6cdd28ac3b1e5719a67fb645161912bef1e472e63995ceb0622`
- x32 harness SHA-256:
  `f91218055763076acda3edb2978dd5a37ab0167514cd3044ee9861212ded6766`
- top: `picorv32_x32_top`
- target: `xcvu3p-ffvc1517-2-e`
- Yosys: `0.33+103`
- OpenPARF: project-local CPU build
- Vivado: `2025.2`
- virtual timing constraint: `clk`, 10.0 ns
- synthesis policy: `logic-only`

The harness instantiates 32 independent upstream-default PicoRV32 cores. Each
core has separately observable inputs and outputs, preventing synthesis from
merging or pruning replicated cores. It is a reproducible scale harness made
from open RTL, not a claim that 32 disconnected CPUs form a representative
application workload.

Run the stages after the project and pinned source are synchronized:

```bash
scripts/remote/proj169-2.sh phase2-arch-large
scripts/remote/proj169-2.sh picorv32-x32-synth
scripts/remote/proj169-2.sh picorv32-x32-openparf
scripts/remote/proj169-2.sh picorv32-x32-vivado
```

## Synthesis and EmuIR metrics

| Metric | Result |
| --- | ---: |
| mapped primitives | 121,984 |
| LUT | 70,880 |
| FF | 51,104 |
| LUT1 / LUT2 / LUT3 | 3,232 / 11,296 / 7,584 |
| LUT4 / LUT5 / LUT6 | 2,304 / 8,640 / 37,824 |
| FDRE / FDSE | 51,008 / 96 |
| Yosys CPU time | 19.81 s |
| Yosys peak memory | 618.52 MB |
| mapped JSON / Verilog | 144.2 MB / 101.2 MB |
| EmuIR size | 250.1 MB |
| EmuIR cut classes | 1 clock, 70,880 combinational, 3,201 primary input, 51,104 register output |

The strict mapped-cell gate counts physical LUT/FF primitives in EmuIR rather
than RTL operators or generic synthesis cells. The result is 21,984 cells
above the requested threshold.

## OpenPARF metrics

| Metric | Result |
| --- | ---: |
| ArchitectureDB sites | 30,038 |
| SLICEL / SLICEM | 14,924 / 15,114 |
| exposed LUT slots | 240,304 |
| exposed FF slots | 240,304 |
| OpenPARF instances | 121,984 |
| emitted nets | 121,026 |
| dropped single-endpoint nets | 4,160 |
| global placement iterations | 443 |
| global placement runtime | 83.714 s |
| legalization runtime | 38.851 s |
| total OpenPARF runtime | 126.357 s |
| legal cells | 121,984 / 121,984 |
| sites used | 14,671 |
| post-legalization HPWL | 1.756144e6 |

The original 15,844-site ArchitectureDB and 100-iteration limit stopped with
high overflow and produced a placement that Vivado rejected as globally
congested. The validated run exposes 30,038 sites and allows up to 1,000
iterations. OpenPARF converged at iteration 443 before legalization.

## Vivado physical-validation metrics

| Metric | Result |
| --- | ---: |
| exact placement identities matched | 121,984 / 121,984 |
| TSV placement import | 104.117 s |
| FF LOC control-set repairs | 136 of 51,104 |
| mapped-netlist synthesis | 59 s |
| Vivado placement | 17:01 elapsed, 5.15 GB peak |
| Vivado routing | 1:01:28 elapsed, 5.55 GB peak |
| logical nets | 123,044 |
| routable nets | 118,961 |
| fully routed nets | 118,961 |
| nets with routing errors | 0 |
| DRC checks found | 0 |
| routed DCP | 39.3 MB |
| 100 MHz setup WNS / TNS | -4.025 ns / -1061.604 ns |
| setup failing endpoints | 941 of 92,128 |
| hold WHS / THS | 0.044 ns / 0.000 ns |

Vivado initially estimated global and timing congestion at level 6. Its
rip-up/reroute pass reduced 78,033 overlapping nodes through
18,711, 6,374, 2,278, 606, 125, 27, 9, 4, 1, and finally 0. Later timing
iterations also returned to zero overlaps. The final route status reports
all 118,961 routable nets fully routed, and the DRC report identifies the
design as `Fully Routed` with zero checks found.

The routed checkpoint is generated at:

```text
build/remote/benchmarks/picorv32-x32-l5/vivado/routed.dcp
```

## Scale issues exposed and fixed

1. The reference placer's original cell-by-site search scaled quadratically.
   Compatible Site/BEL slots are now queued by type.
2. A 56.4 MB per-cell XDC was only about 10% parsed after roughly 20 minutes.
   The flow now writes a hexadecimal-name TSV, streams sorted Vivado cell
   identities, and applies LOC/BEL properties in batches. The complete
   121,984-cell import takes 104 seconds.
3. Vivado adds GND/VCC cells that are absent from EmuIR. They are excluded
   before exact placement identity comparison.
4. UltraScale+ FF control-set compatibility is not fully modeled in the
   OpenPARF adapter. The checker preserves every accepted placement and lets
   Vivado move only the 136 rejected FF LOCs.
5. OpenPARF needed a larger sampled architecture and more than 100 global
   placement iterations to reach a routable density distribution.
6. The port-heavy stress harness creates substantial device-edge routing
   pressure. Vivado routed it successfully, but the one-hour congestion
   repair is not representative of a compact SoC wrapper.

## Koios experiment

Koios DLA was evaluated before adopting the x32 PicoRV32 scale harness.
Logic-only synthesis expands the benchmark's soft memories into very large
logic networks:

- `dla_like.medium` exceeded 64 GB resident memory while still growing and was
  stopped to protect the shared server;
- `dla_like.small` peaked near 79 GB and produced about 8.9 GB mapped JSON plus
  4.5 GB mapped Verilog before filling the remote home filesystem.

Those partial products were deleted and disk space was recovered. Koios
remains the right BRAM/DSP-era benchmark, but it should be run after native
memory and DSP preservation is implemented rather than forced through the
current logic-only policy.

## Interpretation and remaining work

This result proves that the current frontend, EmuIR, OpenPARF adapter,
large-placement importer, legality checker, and Vivado bridge operate on a
strictly counted 121,984-cell design and can produce a fully routed,
DRC-clean UltraScale+ checkpoint.

It does not prove the complete multi-FPGA emulation flow:

- G4 partitioning is now validated separately; G5 system routing, G6
  TDM/transport, and G7 lane/pin assignment are not yet implemented;
- the run is single-FPGA and logic-only;
- 100 MHz setup timing is not closed;
- native CARRY8, LUTRAM, BRAM, DSP48E2, clocking, and macro packing remain;
- Yosys `INIT=1'bx` values are dropped/defaulted by Vivado, so pre-reset
  unknown-state semantics are not preserved;
- RTL-to-mapped sequential equivalence is not yet an automated gate.

The next scale milestone should therefore be forced two-FPGA partitioning on
PicoRV32/AES, followed by system routing and TDM. Koios should return after
native BRAM/DSP support prevents its memories from exploding into soft logic.
