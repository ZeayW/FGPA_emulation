/*
 * Normalize legacy Xilinx helper primitives after logic-only synthesis.
 *
 * Older Yosys xcup flows retain INV cells even when carry, wide-mux, memory,
 * DSP, and SRL inference are disabled. A LUT1 is the equivalent portable
 * physical primitive understood by the current EmuFlow/OpenPARF adapter.
 */

module INV (
    input  wire I,
    output wire O
);
    LUT1 #(
        .INIT(2'b01)
    ) _TECHMAP_REPLACE_ (
        .I0(I),
        .O(O)
    );
endmodule
