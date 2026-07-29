# OpenROAD/TritonPart source provenance

- Upstream: https://github.com/The-OpenROAD-Project/OpenROAD
- Imported commit: `a008522d88b669ac4c985609533cf5a3d2649222`
- OpenROAD license: BSD-3-Clause (`LICENSE`)

This directory contains the OpenROAD implementation source and bundled
third-party source needed to compile the TritonPart provider in the EmuFlow
monorepo. Precompiled objects and libraries are not included.

The two upstream Git submodules are materialized as ordinary source
directories:

- `src/sta/`: The-OpenROAD-Project/OpenSTA commit
  `aa598a2f14c5c142e90391a69988523505e7db3d`, GPL-3.0-or-later
- `third-party/abc/`: The-OpenROAD-Project/abc commit
  `ef5389d31526003c2ebd7e6d6d6fe3848a20f0a2`

Large regression datasets, documentation images, and example design data were
excluded because they are not implementation source and are not required by
an `ENABLE_TESTS=OFF` production build. The TritonPart implementation is
visible under `src/par/`; its OpenDB, OpenSTA, utility, ABC, and
OR-Tools-facing source dependencies are present in the same tree.

Because those regression directories are intentionally absent, their
`add_subdirectory(test)` calls are guarded by source-existence checks. This is
a build-only integration change; no OpenROAD algorithm is replaced or hidden.
