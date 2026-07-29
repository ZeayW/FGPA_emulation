# RePart source provenance

- Upstream: https://github.com/Welement-zyf/RePart
- Imported commit: `211a9d8fd526576387cad7ac6dd3531354aeb31c`
- Upstream license: GPL-3.0-only (`LICENSE`)

The repository contains the RePart implementation source required by EmuFlow,
not its precompiled `partitioner` executable or upstream benchmark datasets.

EmuFlow currently applies the source change recorded in
`../patches/repart-phase3a-disable-replication.patch`. It adds the explicit
`-r 0|1` switch so the unique-owner Phase 3A and replication-aware Phase 3B
contracts can select their intended behavior. Modified RePart source remains
GPL-3.0-only.
