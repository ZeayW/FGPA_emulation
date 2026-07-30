# RePart source provenance

- Upstream: https://github.com/Welement-zyf/RePart
- Imported commit: `211a9d8fd526576387cad7ac6dd3531354aeb31c`
- Upstream license: GPL-3.0-only (`LICENSE`)

The repository contains the RePart implementation source required by EmuFlow,
not its precompiled `partitioner` executable or upstream benchmark datasets.

EmuFlow currently applies the source change recorded in
`../../third_party/patches/repart-phase3a-disable-replication.patch`. It adds the explicit
`-r 0|1` switch so the unique-owner Phase 3A and replication-aware Phase 3B
contracts can select their intended behavior. Modified RePart source remains
GPL-3.0-only.

Phase 3B also adds a `design.rep` per-vertex replicability input and propagates
that property through multilevel contraction. The replication refinement
queue and its final legality guard both exclude masked vertices. This keeps
the academic RePart optimization in C++ while enforcing EmuFlow's
sequential-safety policy before a move is proposed, not merely after output
import.

The upstream headers' machine-specific
`../../boost_1_86_0/include/boost/...` paths are replaced with standard
`<boost/...>` includes. The root CMake target resolves and links Boost, so a
clean checkout builds without an untracked private Boost directory.

The upstream four-thread replication and replica-deletion refiners used a
type name where a mutex instance was required, leaving shared gain queues
unprotected. EmuFlow adds an explicit mutex member to each refiner and locks
queue insertion. This removes the data race required for deterministic,
independently repeatable provider runs.
