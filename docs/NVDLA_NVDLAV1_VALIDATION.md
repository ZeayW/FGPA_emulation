# NVDLA nvdlav1 large-design validation

## Scope

This experiment replaces the replicated PicoRV32 scale harness with a genuine
connected open RTL design. It uses the official NVDLA `nvdlav1` release:

- repository: `https://github.com/nvdla/hw`;
- pinned commit: `8e06b1b9d85aab65b40d43d08eec5ea4681ff715`;
- source archive SHA-256:
  `3dc7270c8975acd439959120ec22ea6c968c06e822f169b0ec3899d74f7fa1b8`;
- license: NVIDIA Open NVDLA License and Agreement v1.0;
- connected top: `NV_nvdla`;
- target: `xcvu3p-ffvc1517-2-e`;
- tool: Vivado 2025.2 on `proj169-2`.

`NV_nvdla` connects the CACC, CDMA, CMAC-A/B, SDP, PDP, CDP, Rubik,
MCIF/CVIF, configuration, interrupt, and clock/reset logic. No compute block
is replicated by EmuFlow and no NVDLA partition is tested in isolation.

## FPGA adaptation boundary

The upstream release contains ASIC SRAM wrappers whose implementations depend
on proprietary MBIST/DFT cells. The experiment generates parameter- and
port-preserving black-box declarations for the 28 wrapper types. Vivado
retained 168 SRAM instances as black boxes. The accelerator compute,
interconnect, register, and control logic remained present.

The synthesis defines are:

```text
SYNTHESIS
DESIGNWARE_NOEXIST
NVDLA_BDMA_ENABLE
NVDLA_CDP_ENABLE
NVDLA_PDP_ENABLE
NVDLA_RUBIK_ENABLE
```

The generated `NV_NVDLA_partition_o.v` uses C-preprocessor-style feature
directives. The preparation step changes only leading `#ifdef`, `#ifndef`,
`#else`, and `#endif` spellings to Verilog backtick directives.

## Successful Vivado result

Vivado completed synthesis with exit status zero and generated a DCP, EDIF,
functional structural Verilog, hierarchical utilization report, and primitive
inventory.

| Metric | Result |
| --- | ---: |
| Wall time | 54:47.72 |
| Maximum RSS (`/usr/bin/time`) | 13,030,944 KB |
| All hierarchical cells | 3,123,117 |
| Primitive cells | 3,115,819 |
| SRAM black-box instances | 168 |
| Total LUTs (`report_utilization`) | 1,825,473 |
| FFs (`report_utilization`) | 915,739 |
| DSP blocks (`report_utilization`) | 459 |
| CARRY8 primitive instances | 14,759 |
| Synthesized DCP size | 294 MB |
| EDIF size | 2.9 GB |
| Functional Verilog size | 1.1 GB |

The six top synthesis partitions consume:

| Hierarchy | LUTs | FFs | DSPs |
| --- | ---: | ---: | ---: |
| `u_partition_a` | 150,002 | 88,629 | 0 |
| `u_partition_c` | 274,941 | 162,128 | 79 |
| `u_partition_ma` | 388,302 | 62,896 | 0 |
| `u_partition_mb` | 388,298 | 62,886 | 0 |
| `u_partition_o` | 368,375 | 373,130 | 144 |
| `u_partition_p` | 255,555 | 166,013 | 236 |

This passes the real-design 100,000-cell scale gate by more than an order of
magnitude. It is also a natural multi-FPGA workload: at the configured 75%
utilization limit, one VU3P provides 295,560 effective LUTs, so LUT capacity
requires at least seven devices. The checked-in virtual target therefore uses
an eight-device 2x4 mesh.

## Import scalability result

The existing bridge reads Vivado's functional Verilog into Yosys, flattens
the design, and writes Yosys JSON before EmuIR import. On this design it is
not viable:

| Metric | Result |
| --- | ---: |
| Elapsed before controlled termination | 16:39.29 |
| Maximum RSS | 118,116,332 KB |
| JSON produced | No |
| Termination | deliberate `SIGTERM` before host memory exhaustion |

The server has 125 GiB RAM. The bridge reached roughly 116 GiB resident
memory, began using swap, and reduced available memory to approximately
6-10 GiB. It was stopped cleanly and the host recovered immediately.

Therefore the validated boundary is:

```text
official NVDLA RTL -> Vivado elaboration/synthesis -> DCP/EDIF/resource report
```

The following boundary has **not** passed for NVDLA and must not be reported
as complete:

```text
Vivado structural netlist -> Yosys JSON -> EmuIR -> TritonPart
```

The next implementation milestone is a streaming Vivado DCP/EDIF-to-EmuIR
import path that does not construct both a complete Yosys RTLIL graph and a
second complete JSON graph in memory. After that importer passes, NVDLA Phase
3 should target `platforms/virtual/xcvu3p_8fpga_mesh.json`.

## Frontend failures retained as regression knowledge

Four earlier attempts failed before the successful configuration:

1. raw `NV_NVDLA_partition_o.v` used unsupported `#ifdef` syntax;
2. excluding `p_SSYNC3DO*` sources left required synchronizer modules absent;
3. an initial RAM stub generator dropped the wrapper parameter and caused
   excess parameter-override errors;
4. Synopsys `DW_minmax` was unresolved until the upstream
   `DESIGNWARE_NOEXIST` fallback was enabled.

These are source-integration failures, not capacity or partitioning results.
The reproducible scripts contain the corresponding fixes.
