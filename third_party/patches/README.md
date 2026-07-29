# Third-party source patches

These patches record modifications already applied to the in-tree upstream
provider source. They are source-code change records, not binary replacements.

`repart-phase3a-disable-replication.patch` modifies GPL-3.0-only RePart and is
distributed under GPL-3.0-only. Additional Phase 3B replicability-mask changes
are documented beside the imported source in
`third_party/repart/EMUFLOW_PROVENANCE.md`. RePart is compiled from this
repository and executed as a process boundary; it is not linked into the
Apache-2.0 EmuFlow control plane.

OpenPARF patches modify BSD-3-Clause source and retain the upstream copyright
and license requirements.
