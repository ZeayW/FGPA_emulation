/*
 * Copyright 2026 EmuFlow contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Fixed-width direction declarations for the public VTR flagship hard-block
 * modes.  After Yosys has checked the mapped design, EmuFlow changes these
 * internal cell types to VTR's standard eBLIF model names.
 */

`define VTR_MULTIPLIER(NAME, WIDTH, OUT_WIDTH) \
(* blackbox *) \
module NAME ( \
	input [WIDTH-1:0] a, \
	input [WIDTH-1:0] b, \
	output [OUT_WIDTH-1:0] out \
); \
endmodule

`VTR_MULTIPLIER(VTR_MULTIPLY_9X9, 9, 18)
`VTR_MULTIPLIER(VTR_MULTIPLY_18X18, 18, 36)
`VTR_MULTIPLIER(VTR_MULTIPLY_36X36, 36, 72)

`define VTR_SP_MODEL(NAME, ABITS) \
(* blackbox *) \
module NAME ( \
	input clk, \
	input we, \
	input [ABITS-1:0] addr, \
	input data, \
	output out \
); \
endmodule

`VTR_SP_MODEL(VTR_SP_BIT_512, 9)
`VTR_SP_MODEL(VTR_SP_BIT_1024, 10)
`VTR_SP_MODEL(VTR_SP_BIT_2048, 11)
`VTR_SP_MODEL(VTR_SP_BIT_4096, 12)
`VTR_SP_MODEL(VTR_SP_BIT_8192, 13)
`VTR_SP_MODEL(VTR_SP_BIT_16384, 14)
`VTR_SP_MODEL(VTR_SP_BIT_32768, 15)

`define VTR_DP_MODEL(NAME, ABITS) \
(* blackbox *) \
module NAME ( \
	input clk, \
	input we1, \
	input we2, \
	input [ABITS-1:0] addr1, \
	input [ABITS-1:0] addr2, \
	input data1, \
	input data2, \
	output out1, \
	output out2 \
); \
endmodule

`VTR_DP_MODEL(VTR_DP_BIT_1024, 10)
`VTR_DP_MODEL(VTR_DP_BIT_2048, 11)
`VTR_DP_MODEL(VTR_DP_BIT_4096, 12)
`VTR_DP_MODEL(VTR_DP_BIT_8192, 13)
`VTR_DP_MODEL(VTR_DP_BIT_16384, 14)
`VTR_DP_MODEL(VTR_DP_BIT_32768, 15)

`undef VTR_MULTIPLIER
`undef VTR_SP_MODEL
`undef VTR_DP_MODEL
