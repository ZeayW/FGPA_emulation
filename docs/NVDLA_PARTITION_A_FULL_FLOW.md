# NVDLA partition-A 731k-cell full-flow validation

## Scope and status

This experiment carries a genuine connected RTL design through EmuFlow's
board-independent multi-FPGA path:

1. logic synthesis and EmuIR import;
2. safe clustering and OpenROAD TritonPart partitioning;
3. system-level multicast routing;
4. TDM lane/slot assignment and transport simulation;
5. per-FPGA split, virtual pin planning, and mapped equivalence;
6. transport synthesis and per-FPGA placement-IR lowering;
7. OpenPARF global placement plus ArchitectureDB legalization;
8. Vivado placement/routing and Phase 7C physical/timing aggregation;
9. Phase 7D reproducible G0-G9 release audit.

The source is the official NVDLA `nvdlav1` repository:

- repository: `https://github.com/nvdla/hw`;
- pinned commit: `8e06b1b9d85aab65b40d43d08eec5ea4681ff715`;
- top: `NV_NVDLA_partition_a`;
- target: four virtual `xcvu9p-flga2104-2L-e` devices in a 2-by-2 mesh;
- OpenROAD seed: `20260727`;
- experiment root:
  `/tmp/ziyiwang21-emuflow/nvdla-partition-a-gated-flow` on `proj169-2`.

The board-independent flow through Phase 7C is complete and passing. This is
a real-design scale and flow-completeness experiment, but it is not a
balanced emulation QoR result. TritonPart's cut-minimum solution puts
all but three one-FF clusters on `fpga0`; the three deterministic repair moves
exist only to exercise the four-FPGA transport and physical backends. This
limitation is explicit in the Phase 3 artifacts.

## Frontend and clock topology

NVDLA partition A instantiates 16 single-port ASIC SRAM wrappers:

- eight `nv_ram_rws_32x512`;
- four `nv_ram_rws_32x544`;
- four `nv_ram_rws_32x768`.

The experiment replaces these wrappers with synthesizable register-array
models that preserve their ports, parameters, synchronous-read behavior, and
storage state. It does not black-box the memories. This expands storage into
LUT/FF logic and is therefore a functional scale-validation policy, not a
final BRAM-aware mapping.

The upstream ASIC clock-gate model is tagged for Vivado gated-clock
conversion. Vivado converts all seven clock gates to legal clock-enable
logic. Phase 1 then independently inspects every FF clock pin and requires
that no LUT drive a clock net.

| Metric | Result |
| --- | ---: |
| EmuIR instances | 731,313 |
| LUTs | 334,522 |
| FFs | 396,791 |
| FF clock nets | 1 |
| Fabric-logic clock nets | 0 |
| Black boxes | 0 |
| Phase 1 wall time | 2:09.85 |
| Phase 1 maximum RSS | 6,069,460 KB |
| Yosys import wall time | 5:55.31 |
| Yosys import maximum RSS | 25,189,620 KB |

The single-VU9P fit check passes at the BoardDB's conservative 75% effective
capacity. The four-device aggregate effective capacity is 3,546,720 LUTs and
7,093,440 FFs.

## Phase 3: TritonPart

The partitioner is the real OpenROAD TritonPart implementation
(`openroad-2.0-17598-ga008522d8`), not EmuFlow's greedy baseline.

EmuFlow first closes user groups, hard macros, and combinational regions into
atomic clusters. Only primary-input and register-output boundaries may be
cut. It then exports a multilevel hypergraph with one vertex per cluster,
three vertex weights (`cells`, `lut`, and `ff`), fixed-part constraints, and
weighted nets. TritonPart optimizes hyperedge cuts under the supplied balance
constraint; EmuFlow reimports the solution and independently checks instance
coverage, capacity, fixed/group constraints, and cut legality.

This run intentionally requests a 300% balance tolerance. Its purpose is to
find the lowest-cut validation partition, not to claim balanced QoR.
TritonPart uses one FPGA in the raw solution. The optional minimum-used-FPGA
repair then moves the smallest legal cluster to each empty device:

| FPGA | Original instances | LUT | FF |
| --- | ---: | ---: | ---: |
| `fpga0` | 731,310 | 334,522 | 396,788 |
| `fpga1` | 1 | 0 | 1 |
| `fpga2` | 1 | 0 | 1 |
| `fpga3` | 1 | 0 | 1 |

The repair moves are `c000018`, `c000019`, and `c000020`; each contains one
FF and adds one estimated unit of cut cost.

| Metric | Result |
| --- | ---: |
| Atomic clusters | 5,938 |
| Hyperedges | 359,396 |
| Cut nets | 3 |
| Cut sink endpoints | 3 |
| Illegal cuts | 0 |
| Used FPGAs | 4 |
| Phase 3 wall time | 2:02.01 |
| Phase 3 maximum RSS | 5,701,336 KB |

This result proves that TritonPart and all downstream multi-FPGA artifact
contracts work at 731k-cell scale. It does not prove useful load balance. A
future timing-aware, state-transfer-capable partitioning stage must address
that separately.

## Phases 4-6: routing, TDM, split, and equivalence

All board-independent gates pass:

| Stage | Algorithm / independent gate | Result |
| --- | --- | ---: |
| System routing | negotiated shortest-path multicast tree | 3 demands, 3 routed sinks |
| Link capacity | independent direction/capacity validation | 4 bit-hops, 0.09765625% max utilization, 0 overloads |
| TDM assignment | deterministic earliest legal slot | 64 slots, completion slot 5, 0 collisions |
| Transport simulation | Python value model | 16 frames, 48 delivered sink values |
| Netlist split | deterministic cut-shadow split | 731,313 instances, 734,228 net segments |
| Lane agreement | two-ended endpoint reconstruction | 4 entries, 8 endpoints |
| Virtual pin planning | BoardDB-independent anchors | 6 anchors, 6 unbound package pins |
| Mapped equivalence | Xilinx LUT/FF cycle model | 2 cycles, 0 mismatches |

The equivalence check covers 396,791 FFs, 334,522 LUTs, 793,582 state bits,
and 1,102 output bits. Its trace SHA-256 is
`f088c2f4f651c8b93cf4278ae5a65e7bc90ca81499ba94580de3146680dfad50`.
Phase 4, Phase 5, and Phase 6 take 0.82 seconds, 0.08 seconds, and 3:31.40,
respectively; Phase 6 peaks at 7,520,580 KB RSS.

The generated runtime uses a 250 MHz fabric clock and releases one pausible
DUT edge per 64-slot frame. Completion occurs in slot 5, the global barrier
releases in slot 63, and 58 slots (232 ns) are reserved for shadow-state
settling. The nominal virtual DUT rate is 3.90625 MHz.

## Phase 7A: transport lowering and OpenPARF

Each partition's generated transport RTL is synthesized with Yosys and
stitched into its exact logical netlist. A lowering bug exposed by the
RX-only main partition was fixed: dummy `source_values`/`shadow_values`
interface nets are now consumed when their top-level ports are removed.

| FPGA | Original cells | Transport cells | Merged cells | Merged nets | Legal sites |
| --- | ---: | ---: | ---: | ---: | ---: |
| `fpga0` | 731,310 | 21 | 731,331 | 734,301 | 55,756 |
| `fpga1` | 1 | 19 | 20 | 57 | 3 |
| `fpga2` | 1 | 17 | 18 | 24 | 3 |
| `fpga3` | 1 | 17 | 18 | 24 | 3 |
| **Total** | **731,313** | **74** | **731,387** |  |  |

The three small partitions use OpenPARF's direct legal placement. For the
main partition:

1. OpenPARF optimizes continuous global coordinates for all 731,331 cells;
2. EmuFlow imports every coordinate;
3. the complete 147,780-SLICE VU9P ArchitectureDB assigns a unique compatible
   Site/BEL near that coordinate with 25% local headroom;
4. the normal placement validator checks exact instance coverage, resource
   compatibility, and Site/BEL uniqueness.

OpenPARF global placement takes 8:12.40 and peaks at 5,667,400 KB RSS.
ArchitectureDB legalization takes 8:06.01 and peaks at 7,967,884 KB RSS.
Both stages exit normally and the 731,331-cell placement is legal.

## Phase 7B: physical placement strategy

The main VU9P is a multi-SLR device. Exact or overly dense OpenPARF LOC/BEL
constraints can be individually legal while still demanding more
super-long-line (SLL) crossings than the package provides. The physical
handoff therefore treats OpenPARF as a global-placement guide, not as a
complete UltraScale+ packer.

The structural emitter preserves exact cell identities and indexes pins by
instance, avoiding the earlier quadratic scan. Unknown FF `INIT=x` values are
now omitted rather than emitted as invalid Vivado primitive properties; the
current main partition contains nine such unspecified initial values.

The primary strategy fixes at most one LUT on a deterministic 1/64 sample of
OpenPARF-used SLICE sites. This leaves all FFs and remaining LUTs movable for
Vivado's control-set, clock-region, SLR, placement, and routing repair while
retaining 870 physical OpenPARF anchors.

The negative controls are important:

- exact full-device placement required at least 110,927 SLR crossings;
- one fixed LUT on every one of the 55,756 OpenPARF-used sites failed
  placement, requiring 159,340 SLLs between SLR0 and SLR1 where 17,280 are
  available;
- earlier one-SLR compression placed successfully but failed routing at
  82.88% vertical-wire utilization;
- an earlier ungated-clock coarse run reached routing but stopped with 14,296
  overlap nodes and exposed the unsafe fabric-clock topology.

These are recorded failures, not successful physical results.

All four partitions pass Vivado placement and routing:

| FPGA | Mapped cells | Physical cells | Infrastructure | Routing errors | WNS | Wall time |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `fpga0` | 731,331 | 731,332 | 1 BUFGCE | 0 | +1.435 ns | 1:10:13 |
| `fpga1` | 20 | 20 | 0 | 0 | +2.842 ns | 1:32.20 |
| `fpga2` | 18 | 18 | 0 | 0 | +2.957 ns | 1:31.59 |
| `fpga3` | 18 | 18 | 0 | 0 | +2.957 ns | 1:31.81 |

The main partition preserves all 731,331 mapped identities and all 870
OpenPARF anchors. Vivado inserts one `BUFGCE` for a 3,639-load net during
post-placement optimization. Phase 7C reopens the routed checkpoint, matches
every mapped cell name against the pre-placement inventory, and permits only
explicitly recorded `BUFG*` physical infrastructure. The added cell is:

`u_partition_a_reset.sync_reset_synced_rstn.NV_GENERIC_CELL.accu2sc_credit_vld_i_1_bufg_place`

Main-partition routing converges from 85,289 overlapping nodes through 2,255,
179, 24, and 7 to zero. Final global vertical and horizontal routing
utilization are 12.8923% and 12.6307%. All 679,250 routable nets are fully
routed, the default DRC rule deck finds zero checks, setup WNS is +1.435 ns,
and hold WHS is +0.019 ns. Phase 7B peaks at 11,883,380 KB RSS.

## Phase 7C: independent physical and runtime closure

Phase 7C does not trust the in-process `route_design` return alone. It
sequentially reopens all four routed DCPs and reruns cell-identity,
route-status, DRC, and timing queries. The aggregate passes:

| Metric | Result |
| --- | ---: |
| Original design cells | 731,313 |
| Transport cells | 74 |
| Routed mapped cells | 731,387 |
| Tool-inserted infrastructure cells | 1 |
| Total physical cells | 731,388 |
| Unrouted nets | 0 |
| DRC violations | 0 |
| Worst overall/fabric WNS | +1.435 ns |
| Main DUT-to-DUT WNS | +94.274 ns |
| Main fabric-to-DUT WNS | +230.680 ns |

All three timing path classes exist on the main partition. Each tiny
partition contains only one original DUT FF, so DUT-to-DUT and
fabric-to-DUT path classes are genuinely empty there; their
`path_present=0` and conservative zero slack make the aggregate
`worst_dut_wns_ns` and `worst_fabric_to_dut_wns_ns` zero without inventing
margin.

The four-controller RTL testbench also passes 64 frames and three deliberate
stall cycles. The final machine-readable Phase 7C and QoR reports have
`status: pass`.

## Phase 7D: G0-G9 release audit

The final board-independent gate reloads all logical and physical reports
rather than trusting their filenames. It rehashes 376 pinned NVDLA source
dependencies and 26 release-critical artifacts, including every per-FPGA
netlist, OpenPARF placement, mapped Verilog file, and routed DCP.

The NVDLA mesh exposed and fixed an audit assumption hidden by the earlier
single-hop PicoRV32 run: three cut demands produce four scheduled bit-hops
because the `fpga3` sink requires two links. Phase 7D now independently
cross-checks demand count, sink-endpoint count, and bit-hop count instead of
incorrectly requiring them to be identical.

| Gate | NVDLA evidence |
| --- | --- |
| G0-G2 | 376 source dependencies rehashed; elaboration and mapped synthesis pass |
| G3 | 731,313 EmuIR instances |
| G4 | 3 legal cuts across 4 FPGAs |
| G5 | 3 routed sinks, zero overload |
| G6 | 4 scheduled bit-hops, 2 equivalent cycles |
| G7 | 4 logical lanes, 6 virtual anchors |
| G8 | 731,387 legal OpenPARF-placed cells |
| G9 | 731,387 routed mapped cells, +1.435 ns worst WNS |

The audit executes twice. The primary run takes 16.00 seconds and peaks at
22,784 KB RSS; the repeat takes 15.77 seconds and peaks at 21,284 KB RSS.
Both 79,237-byte manifests are byte-identical:

```text
cc965f733830ec6a32c5357a516b36a96b160c8f85ac23ad26714507a713ef0a
```

The manifest records auditor source commit
`ca6501403a7460f52990c7a1b7974dad8854462c`.

## Reproduction

After Phases 3-6 and the runtime-timing contract exist at `ROOT`, run:

```bash
scripts/remote/nvdla_partition_a_phase7.sh phase7a ROOT
scripts/remote/nvdla_partition_a_phase7.sh phase7b ROOT
scripts/remote/nvdla_partition_a_phase7.sh phase7c-finalize ROOT
scripts/remote/nvdla_partition_a_phase7.sh phase7d ROOT
```

The large partition's physical policy can be varied without editing code:

```bash
EMUFLOW_MAIN_ANCHOR_MODULUS=64 \
EMUFLOW_MAIN_PLACE_DIRECTIVE=SSI_SpreadLogic_high \
EMUFLOW_MAIN_ROUTE_DIRECTIVE=Default \
  scripts/remote/nvdla_partition_a_phase7.sh phase7b ROOT
```

`phase7c-finalize` reopens all four routed DCPs, requires exact mapped-cell
identity, explicitly whitelisted physical infrastructure, zero unrouted nets,
zero DRC violations, and non-negative overall, DUT, fabric, and
fabric-to-DUT slack. It then writes
`physical_summary.json`, reruns the controller simulation, and converts the
pending Phase 7C report into `status: pass`.

A tiny one-FF partition has no DUT-to-DUT launch/capture pair. The timing
collector records such an empty path class explicitly as `path_present=0`
with conservative `slack=0.0`; it never substitutes a positive margin. Any
path class that exists must still have non-negative slack.

`phase7d` requires the deployment's `.emuflow-source-commit` marker and
executes the complete audit twice. It rejects source hash changes,
cross-phase demand/sink/hop disagreement, missing FPGA reports, placement or
emission count changes, route/DRC/timing failure, and release-artifact hash
changes.

## Board boundary

No physical board has been selected. This run therefore validates virtual
pin assignment and FPGA-internal placement/routing in Vivado out-of-context
mode. The out-of-context DRC reports explicitly note that connectivity checks
requiring a top-level board design cannot run. This experiment does not claim
package pin assignment, IOSTANDARD selection, clock-source binding, link
training, signal-integrity closure, bitstream generation, or hardware
execution. Those gates require a board-specific BSP. The six unbound virtual
anchors are the explicit handoff boundary.

## Next algorithmic milestone

After physical closure, the next milestone is partition quality rather than
another artificial replication:

1. preserve native BRAM, DSP, and explicit sequential macros;
2. pass synthesis slack/criticality into TritonPart net weights;
3. add safe state-transfer or checkpoint boundaries where large sequential
   regions otherwise remain indivisible;
4. rerun this same top with a meaningful balance constraint and require
   materially better four-FPGA utilization;
5. bind virtual anchors only after a concrete UltraScale+ board BSP is chosen.
