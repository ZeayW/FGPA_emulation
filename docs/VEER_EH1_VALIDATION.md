# VeeR EH1 connected-CPU validation

The pinned official CHIPS Alliance VeeR EH1 commit
`d04b1c7ae675a63dc4307cacfd10547ec937b928` was synthesized with its upstream
FPGA-oriented configuration and `veer_wrapper` top on Vivado 2025.2.

| Metric | Result |
| --- | ---: |
| Vivado wall time | 3:50.07 |
| Vivado maximum RSS | 3,752,408 KB |
| Vivado hierarchical cells | 52,814 |
| EmuIR instances | 48,095 |
| EmuIR nets | 58,462 |
| LUT | 32,915 |
| FF | 14,341 |
| CARRY8 | 324 |
| BRAM18K equivalents | 56 |
| DSP48E2 | 4 |

Vivado synthesis, Yosys structural-netlist conversion, EmuIR import, Phase 1,
TritonPart execution, and the independent Phase 3 checker all completed.
TritonPart used 497,640 KB and 7.24 seconds.

This is a useful connected medium-large regression, but it is not a valid
multi-FPGA quality benchmark on two VU3P devices. The design fits comfortably
on one FPGA. With the two-part minimum enabled, TritonPart placed 48,094 cells
on `fpga0` and only one disconnected `other` cell on `fpga1`, producing zero
cut nets. The result validates scale and interfaces, not partition quality.

The experiment motivated the move to the much larger connected NVDLA top,
whose results are recorded in `docs/NVDLA_NVDLAV1_VALIDATION.md`.
