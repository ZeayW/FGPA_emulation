# End-to-end RTL benchmark validation plan

## 1. Purpose

The benchmark campaign validates the complete EmuFlow path from open RTL,
rather than beginning from pre-synthesized ISPD Bookshelf netlists:

```text
open RTL
  -> synthesis and technology mapping
  -> EmuIR
  -> clustering and multi-FPGA partitioning
  -> board-level routing
  -> TDM scheduling
  -> logical lane assignment
  -> per-FPGA netlist and transport RTL
  -> OpenPARF placement
  -> FPGA routing and Vivado validation
```

ISPD 2016 remains useful for isolated placement-algorithm comparisons, but it
cannot validate synthesis, semantic cut rules, transport insertion, TDM, or
cycle equivalence. It is therefore outside this end-to-end campaign.

## 2. Two independent scale controls

The campaign increases difficulty along two axes:

1. **RTL and physical complexity**: LUT/FF only, then carry and LUTRAM, then
   BRAM/DSP, SystemVerilog dependencies, multiple clocks, and generated RTL.
2. **Emulation topology complexity**: one FPGA, then forced 2-, 4-, and 8-FPGA
   virtual platforms with increasingly constrained link bandwidth.

A design does not need to exceed a physical UltraScale+ device before the
multi-FPGA stages can be tested. A Virtual BoardDB profile can reduce effective
per-FPGA capacity or fix major hierarchy blocks to different virtual FPGAs.
This deliberately creates legal cut nets and exercises routing, TDM, lane
assignment, and transport generation while the designs are still small enough
to debug.

## 3. Validation ladder

| Level | Workload | Expected scale/features | Topology | Main purpose |
| --- | --- | --- | --- | --- |
| L0 | checked-in counter fixture | 4 LUT + 4 FF | 1 FPGA | Interface and schema smoke test only |
| L1 | SERV | roughly 125 LUT + 164 FF upstream | 1 FPGA | First real RTL closed loop |
| L2 | PicoRV32 minimal/regular/large | roughly 0.8k-2k LUT plus carry/LUTRAM | 1 FPGA, then forced 2 FPGA | Mapping growth and first partition regression |
| L3 | secworks AES | roughly 3k LUT + 3k FF | 1 FPGA, then forced 2/4 FPGA | Dense sequential logic, cut quality, TDM simulation |
| L4 | VTR classic and Ibex | mixed Verilog; parameterized SystemVerilog CPU | 1/2/4 FPGA | Frontend diversity, packages, memories, hierarchy |
| L5 scale gate | PicoRV32 x32 | 121,984 mapped LUT/FF primitives | 1 FPGA | 100k-cell frontend, OpenPARF, import and routing scalability |
| L5 | Koios `gemm_layer`, `attention_layer`, `conv_layer` | medium DL datapaths | forced 2/4 FPGA | Wide datapaths, high fanout, early BRAM/DSP work |
| L6 | Koios `dla_like.medium/large`, `tpu_like.large`, proxy variants | large DL/CAD designs | 4/8 FPGA | Capacity, runtime, hard-block grouping, congestion |
| L7 | NVDLA `nvdlav1` | 2048 INT8 MACs plus large memory/control hierarchy | 8+ virtual FPGA | Final frontend and system-scale stress test |

The levels are gates, not a list to run blindly. A level advances only after
the independent checkers pass. If a level fails, the smallest benchmark that
reproduces the same primitive or semantic feature becomes the regression.

## 4. Per-level acceptance gates

Every workload records a machine-readable run manifest and must pass the
applicable gates in order:

| Gate | Boundary | Required evidence |
| --- | --- | --- |
| G0 | source acquisition | pinned commit, license, source list, top and parameters |
| G1 | RTL elaboration | deterministic file order and a clean elaborated hierarchy |
| G2 | synthesis | cell/resource report plus RTL-to-netlist equivalence where supported |
| G3 | EmuIR import | endpoint, clock/reset, resource and stable-name validators pass |
| G4 | partitioning | exact coverage, capacity, group/fixed constraints and cut legality pass |
| G5 | system routing | every sink reached, no cycle and independently recomputed utilization |
| G6 | TDM and transport | no slot collision, precedence holds and partitioned RTL is cycle-equivalent |
| G7 | lane/pin planning | endpoint agreement and virtual lane electrical rules pass |
| G8 | OpenPARF placement | complete, compatible, collision-free Site/BEL placement |
| G9 | FPGA routing | routed DCP, zero unrouted nets, DRC and timing reports |
| G10 | hardware | bitstream, link training and golden workload; deferred until a board is selected |

G0-G9 form the board-independent/virtual-board complete flow. Package-pin
binding and G10 require a real board support package; logical lane assignment
does not.

Connected PicoRV32 now has a sealed G0-G9 Phase 7D release manifest that
rehashes the source and 18 critical artifacts and cross-checks all phase
counts. See `docs/PHASE7D_VALIDATION.md`.

## 5. Feature progression

Each benchmark is first run in the simplest policy that preserves function,
then in the native UltraScale+ policy:

1. **Logic-only policy**: map arithmetic and memories to ordinary LUT/FF logic.
   This isolates scalable placement, partitioning, and TDM infrastructure.
2. **Carry/LUTRAM policy**: enable CARRY8-compatible arithmetic and distributed
   memory packing.
3. **BRAM/DSP policy**: preserve RAMB18/36 and DSP48E2 macros, their cascades,
   fixed groups, and architecture constraints.
4. **Full clocking policy**: preserve multiple clocks and clock-domain metadata.

Logic-only results are regression evidence, not final QoR numbers.

## 6. Execution order

The immediate sequence is:

1. make SERV pass G0-G3 and the existing G8-G9 single-FPGA path — completed;
2. make PicoRV32 pass the same logic-only path — completed; native
   carry/LUTRAM remains a separate physical-backend milestone;
3. pass a strict 100,000-cell frontend and physical scale gate — completed
   with 121,984-cell PicoRV32 x32; fully routed and DRC-clean, but 100 MHz
   setup timing remains open;
4. implement Phase 3 partitioning and force PicoRV32 onto 2 virtual FPGAs —
   completed with 140 legal register-output cuts and zero illegal cuts; AES
   and 4-FPGA QoR remain follow-on coverage;
5. implement system routing, TDM, splitting, lane planning, and mapped DUT
   cycle equivalence — completed for all 140 connected-PicoRV32 bit-hops with
   zero collision and 64 passing virtual cycles;
6. close G8-G9 independently for every generated per-FPGA netlist — completed
   for connected PicoRV32 with integrated frame controllers, 4,223 routed
   cells, zero unrouted nets/DRC violations, and positive DUT, fabric, and
   cross-domain timing margins;
7. introduce Koios with native BRAM/DSP preservation; the current logic-only
   policy expands DLA soft memories beyond practical server memory;
8. use NVDLA only after Koios large is stable.

This ordering keeps failures attributable: only one new scale or primitive
class is introduced at a time.
