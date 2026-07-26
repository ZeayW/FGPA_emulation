# PicoRV32 L2 validation

## Result

PicoRV32 completed the current single-FPGA physical path on `proj169-2`:

```text
pinned PicoRV32 RTL
  -> Yosys xcup logic-only synthesis
  -> EmuIR
  -> OpenPARF global placement and legalization
  -> EmuFlow legality and name-restoration checks
  -> Vivado control-set spill repair
  -> Vivado placement, routing, DRC, and timing
  -> routed DCP
```

Validation date: 2026-07-26.

## Reproducible inputs

- PicoRV32 revision: `87c89acc18994c8cf9a2311e871818e87d304568`
- source SHA-256:
  `0836050971b3c6cdd28ac3b1e5719a67fb645161912bef1e472e63995ceb0622`
- top: `picorv32`, with upstream default parameters
- target: `xcvu3p-ffvc1517-2-e`
- Yosys: `0.33+103`
- OpenPARF: project-local CPU build
- Vivado: `2025.2`
- virtual timing constraint: `clk`, 10.0 ns
- synthesis policy: `logic-only`

The default configuration keeps the full 32-register, dual-port register file
and both 64-bit counters. Optional compressed ISA, multiplier, divider, PCPI,
IRQ, and barrel shifter features are disabled by the upstream defaults.
Logic-only mapping expands the register file to FFs and deliberately disables
carry, wide mux, DSP, BRAM, LUTRAM, and SRL mapping.

Run the complete remote validation:

```bash
scripts/remote/proj169-2.sh picorv32-l2-all
```

When the project, source, ArchitectureDB, and OpenPARF installation are already
synchronized:

```bash
scripts/remote/proj169-2.sh picorv32-l2
```

## Observed metrics

| Boundary | Result |
| --- | --- |
| RTL source files | 1 |
| mapped primitives | 3812 |
| LUT | 2215 |
| FF | 1597 |
| scale versus SERV | 8.7x mapped primitives |
| EmuIR nets | 3914 cut-classified nets |
| OpenPARF emitted nets | 3784 |
| dropped single-endpoint nets | 130 |
| ArchitectureDB sample | 483 SLICE sites, 3864 LUT and FF slots |
| OpenPARF placement | 3812/3812 cells legal |
| OpenPARF SLICE sites used | 461 |
| OpenPARF placement runtime | 6.1 seconds |
| FF LOC spill repairs | 4 of 1597 |
| Vivado logical/routable nets | 3849 / 3742 |
| fully routed nets | 3742 |
| routing errors | 0 |
| DRC checks found | 0 |
| 100 MHz setup WNS/TNS | 2.781 ns / 0.000 ns |
| hold WHS/THS | 0.061 ns / 0.000 ns |
| routed DCP size | 1.8 MiB |

Vivado elapsed times were approximately 28 seconds for mapped-netlist
synthesis, 11 seconds for XDC import, 61 seconds for placement completion, and
26 seconds for routing.

The routed checkpoint is generated at:

```text
build/remote/benchmarks/picorv32-l2/vivado/routed.dcp
```

## Scale issues exposed and fixed

1. The 64-SLICE ArchitectureDB used for SERV could not hold PicoRV32. The
   remote export window now requests 512 sites and obtains 483 real SLICE
   sites from the compact device region.
2. Vivado initially optimized away 30 constant-control FFs while reading the
   Yosys mapped Verilog. Mapped cells now carry canonical
   `DONT_TOUCH="yes"` and `KEEP="yes"` attributes, preserving the one-to-one
   EmuIR/OpenPARF/Vivado identity contract.
3. OpenPARF's current adapter models FF slot capacity but not the complete
   UltraScale+ CKEN/control-set compatibility relation. Four FF LOC constraints
   were rejected because unrelated CE nets shared a SLICE. The validation
   harness records these repairs and lets Vivado spill only those FFs; all
   2215 LUT LOC/BEL decisions remain fixed.

## Interpretation and remaining work

L2 proves that the current physical bridge scales from hundreds to thousands
of primitives and can route a dense register-file CPU. It does not yet prove
native UltraScale+ QoR or the multi-FPGA flow. The next physical-backend work
is explicit OpenPARF control-set modeling, native CARRY8/LUTRAM packing, and
automated sequential equivalence. The next benchmark is secworks AES, but it
should be paired with Phase 3 partitioning so it exercises a real multi-FPGA
boundary rather than only a larger single-FPGA placement.
