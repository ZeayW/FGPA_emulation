# Yosys source provenance

- Upstream: https://github.com/YosysHQ/yosys
- Release: `v0.57`
- Commit: `3aca86049e79a165932e3e7660358376f45acaed`
- License: ISC (`COPYING`)

The upstream submodules required by this release are materialized as ordinary
source directories so that a clone of this repository is self-contained:

- `abc/`: YosysHQ/abc commit
  `8827bafb7f288de6749dc6e30fa452f2040949c0`
- `libs/cxxopts/`: jarro2783/cxxopts commit
  `4bf61f08697b110d9e3991864650a405b3dd515d`

No prebuilt Yosys or ABC executable is stored in this repository. The EmuFlow
top-level build compiles this source tree and the synthesis phase uses that
build product.
