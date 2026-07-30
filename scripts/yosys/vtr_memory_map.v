/*
 * Copyright 2026 EmuFlow contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Lower memory_libmap's exact VTR memory modes to the standard architecture
 * model port names consumed by VPR.
 */

`define VTR_SP_MEMORY(INTERNAL, MODEL, ABITS, WIDTH) \
module INTERNAL (...); \
	input CLK_C; \
	input PORT_A_CLK; \
	input [ABITS-1:0] PORT_A_ADDR; \
	input [WIDTH-1:0] PORT_A_WR_DATA; \
	input PORT_A_WR_EN; \
	output [WIDTH-1:0] PORT_A_RD_DATA; \
	genvar bit_index; \
	generate for (bit_index = 0; bit_index < WIDTH; bit_index = bit_index + 1) begin: bits \
		MODEL bit_cell ( \
			.clk(CLK_C), \
			.addr(PORT_A_ADDR), \
			.data(PORT_A_WR_DATA[bit_index]), \
			.we(PORT_A_WR_EN), \
			.out(PORT_A_RD_DATA[bit_index]) \
		); \
	end endgenerate \
endmodule

`VTR_SP_MEMORY(\$__VTR_SP_512X64_ , VTR_SP_BIT_512, 9, 64)
`VTR_SP_MEMORY(\$__VTR_SP_1024X32_ , VTR_SP_BIT_1024, 10, 32)
`VTR_SP_MEMORY(\$__VTR_SP_2048X16_ , VTR_SP_BIT_2048, 11, 16)
`VTR_SP_MEMORY(\$__VTR_SP_4096X8_ , VTR_SP_BIT_4096, 12, 8)
`VTR_SP_MEMORY(\$__VTR_SP_8192X4_ , VTR_SP_BIT_8192, 13, 4)
`VTR_SP_MEMORY(\$__VTR_SP_16384X2_ , VTR_SP_BIT_16384, 14, 2)
`VTR_SP_MEMORY(\$__VTR_SP_32768X1_ , VTR_SP_BIT_32768, 15, 1)

`define VTR_DP_MEMORY(INTERNAL, MODEL, ABITS, WIDTH) \
module INTERNAL (...); \
	input CLK_C; \
	input PORT_A_CLK; \
	input PORT_B_CLK; \
	input [ABITS-1:0] PORT_A_ADDR; \
	input [ABITS-1:0] PORT_B_ADDR; \
	input [WIDTH-1:0] PORT_A_WR_DATA; \
	input [WIDTH-1:0] PORT_B_WR_DATA; \
	input PORT_A_WR_EN; \
	input PORT_B_WR_EN; \
	output [WIDTH-1:0] PORT_A_RD_DATA; \
	output [WIDTH-1:0] PORT_B_RD_DATA; \
	genvar bit_index; \
	generate for (bit_index = 0; bit_index < WIDTH; bit_index = bit_index + 1) begin: bits \
		MODEL bit_cell ( \
			.clk(CLK_C), \
			.addr1(PORT_A_ADDR), \
			.addr2(PORT_B_ADDR), \
			.data1(PORT_A_WR_DATA[bit_index]), \
			.data2(PORT_B_WR_DATA[bit_index]), \
			.we1(PORT_A_WR_EN), \
			.we2(PORT_B_WR_EN), \
			.out1(PORT_A_RD_DATA[bit_index]), \
			.out2(PORT_B_RD_DATA[bit_index]) \
		); \
	end endgenerate \
endmodule

`VTR_DP_MEMORY(\$__VTR_DP_1024X32_ , VTR_DP_BIT_1024, 10, 32)
`VTR_DP_MEMORY(\$__VTR_DP_2048X16_ , VTR_DP_BIT_2048, 11, 16)
`VTR_DP_MEMORY(\$__VTR_DP_4096X8_ , VTR_DP_BIT_4096, 12, 8)
`VTR_DP_MEMORY(\$__VTR_DP_8192X4_ , VTR_DP_BIT_8192, 13, 4)
`VTR_DP_MEMORY(\$__VTR_DP_16384X2_ , VTR_DP_BIT_16384, 14, 2)
`VTR_DP_MEMORY(\$__VTR_DP_32768X1_ , VTR_DP_BIT_32768, 15, 1)

`undef VTR_SP_MEMORY
`undef VTR_DP_MEMORY
