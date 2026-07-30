# EmuFlow provenance

This directory is a source-complete, build-focused snapshot of
[Verilog-to-Routing](https://github.com/verilog-to-routing/vtr-verilog-to-routing)
at commit `a3e60c31bb4384373d2d1a43c38c7569723733b6`.

The snapshot retains the editable VPR, routing-resource graph, architecture,
timing, packing, placement, and routing sources needed by EmuFlow. VTR
benchmarks, documentation generators, GUI/server support, Odin, Parmys, and
the duplicate ABC frontend are not part of this build-focused import.
EmuFlow builds its separate pinned Yosys/ABC source for logic synthesis.

VTR manually synchronizes pugixml 1.7 in
`libs/EXTERNAL/libpugixml`. The following VTR submodules are materialized as
ordinary editable source so a fresh clone does not need recursive downloads:

- libsdcparse: `7a49e2c9ad469d314aa7ec07d3e893ecabd7d9dc`; and
- yaml-cpp: `2decf96e915d2b0c26c68c1659665789dfef2633`.

The root EmuFlow build disables VTR GUI, server, Cap'n Proto, FPGA
Interchange, NoC SAT routing, and parallel execution-engine options. It builds
the `vpr` target from this tree and copies only the resulting disposable
executable below the EmuFlow build directory.

One build-system-only patch adds `VTR_VCS_REVISION_OVERRIDE`. The root build
sets it to the pinned upstream VTR commit so `vpr --version` reports VTR's
source identity instead of the enclosing EmuFlow Git revision.

The upstream `LICENSE.md` and the license files within materialized
dependencies remain authoritative. EmuFlow does not relicense this source.
