# OpenPARF source provenance

- Upstream: https://github.com/PKU-IDEA/OpenPARF
- Imported commit: `793cac2bb109e5cc76046f87d39ed70fa093cf60`
- Upstream license: BSD-3-Clause (`LICENSE`)

This directory contains the OpenPARF build source used by EmuFlow, including
the small upstream build-time benchmark directory. Generated build products,
precompiled extension modules, and Git-LFS unit-test datasets are not
included. The root build configures OpenPARF with `BUILD_TESTING=OFF`.

The imported source includes the checked EmuFlow changes recorded in:

- `../../patches/openparf/export-global-placement.patch`
- `../../scripts/openparf/patches/torch-public-dispatch.patch`
- `../../scripts/openparf/patches/torch-fft-api.patch`
- `../../scripts/openparf/patches/torch-fft-shape.patch`

The vendored `openparf/placement/placer.py` also contains a small
source-visible PyTorch API compatibility change: deterministic mode uses
`torch.use_deterministic_algorithms` when available and retains the legacy
fallback for older PyTorch releases.

OpenPARF's upstream Git submodules are vendored as ordinary source
directories so a clone of EmuFlow does not need recursive submodule checkout:

| Path | Upstream | Revision |
| --- | --- | --- |
| `cmake/Ccache.cmake` | `TheLartians/Ccache.cmake` | `2f890b8d3bd810442c482797c24c7b3d97755215` |
| `thirdparty/blend2d` | `blend2d/blend2d` | `405bcc3b57524b9a6b11cfeecf99c01fe34839dc` |
| `thirdparty/googletest` | `google/googletest` | `bc860af08783b8113005ca7697da5f5d49a8056f` |
| `thirdparty/pugixml` | `zeux/pugixml` | `80a531ee1dc13269d148fd0b1cedbcd7352f7c68` |
| `thirdparty/pybind11` | `pybind/pybind11` | `f70165463328c218d118204efc13aac93783d17b` |
| `thirdparty/yaml-cpp` | `jbeder/yaml-cpp` | `0e6e28d1a38224fc8172fae0109ea7f673c096db` |

Each vendored tree retains its upstream license. The root build defaults to
CPU PyTorch operators for portability. CUDA remains a source build option
when a PyTorch/CUDA toolkit pair is explicitly supplied.

OpenPARF's optional `fpga-router` subtree is excluded from the default
placement build because it currently requires the proprietary GUROBI solver.
The source remains vendored for provenance, but
`EMUFLOW_OPENPARF_BUILD_EXPERIMENTAL_ROUTER` defaults to `OFF`. Enabling that
option is an explicitly non-open experimental configuration and must not
satisfy a source-complete release gate. EmuFlow's system-level router is a
separate in-tree component; an open detailed FPGA-routing backend remains a
distinct integration milestone.

The sparse-direct-legalizer experiment remains an optional source patch and is
not part of the default monorepo build.

Large router example netlists are omitted because they are input data rather
than implementation source.
