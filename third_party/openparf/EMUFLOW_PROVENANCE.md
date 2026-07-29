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

The sparse-direct-legalizer experiment remains an optional source patch and is
not part of the default monorepo build.

Large router example netlists are omitted because they are input data rather
than implementation source.
