/*
 * Copyright 2026 EmuFlow contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Architecture-facing Yosys techmap for the standard VTR "multiply" model.
 * The three widths match the public VTR flagship architecture.  Unsupported
 * multipliers deliberately remain as $mul cells and are mapped to LUTs later.
 */

(* techmap_celltype = "$mul" *)
module _80_vtr_multiply (A, B, Y);
	parameter A_SIGNED = 0;
	parameter B_SIGNED = 0;
	parameter A_WIDTH = 1;
	parameter B_WIDTH = 1;
	parameter Y_WIDTH = 1;

	(* force_downto *) input [A_WIDTH-1:0] A;
	(* force_downto *) input [B_WIDTH-1:0] B;
	(* force_downto *) output [Y_WIDTH-1:0] Y;

	generate
		if (A_WIDTH <= 9 && B_WIDTH <= 9 && A_WIDTH > 1 && B_WIDTH > 1) begin
			wire [8:0] a_ext =
				A_SIGNED ? {{(9-A_WIDTH){A[A_WIDTH-1]}}, A} :
				           {{(9-A_WIDTH){1'b0}}, A};
			wire [8:0] b_ext =
				B_SIGNED ? {{(9-B_WIDTH){B[B_WIDTH-1]}}, B} :
				           {{(9-B_WIDTH){1'b0}}, B};
			wire [17:0] product;
			VTR_MULTIPLY_9X9 hard_multiply (
				.a(a_ext),
				.b(b_ext),
				.out(product)
			);
			if (A_SIGNED || B_SIGNED)
				assign Y = $signed(product);
			else
				assign Y = product;
		end else if (
			A_WIDTH <= 18 && B_WIDTH <= 18 &&
			A_WIDTH > 1 && B_WIDTH > 1
		) begin
			wire [17:0] a_ext =
				A_SIGNED ? {{(18-A_WIDTH){A[A_WIDTH-1]}}, A} :
				           {{(18-A_WIDTH){1'b0}}, A};
			wire [17:0] b_ext =
				B_SIGNED ? {{(18-B_WIDTH){B[B_WIDTH-1]}}, B} :
				           {{(18-B_WIDTH){1'b0}}, B};
			wire [35:0] product;
			VTR_MULTIPLY_18X18 hard_multiply (
				.a(a_ext),
				.b(b_ext),
				.out(product)
			);
			if (A_SIGNED || B_SIGNED)
				assign Y = $signed(product);
			else
				assign Y = product;
		end else if (
			A_WIDTH <= 36 && B_WIDTH <= 36 &&
			A_WIDTH > 1 && B_WIDTH > 1
		) begin
			wire [35:0] a_ext =
				A_SIGNED ? {{(36-A_WIDTH){A[A_WIDTH-1]}}, A} :
				           {{(36-A_WIDTH){1'b0}}, A};
			wire [35:0] b_ext =
				B_SIGNED ? {{(36-B_WIDTH){B[B_WIDTH-1]}}, B} :
				           {{(36-B_WIDTH){1'b0}}, B};
			wire [71:0] product;
			VTR_MULTIPLY_36X36 hard_multiply (
				.a(a_ext),
				.b(b_ext),
				.out(product)
			);
			if (A_SIGNED || B_SIGNED)
				assign Y = $signed(product);
			else
				assign Y = product;
		end else begin
			wire _TECHMAP_FAIL_ = 1;
		end
	endgenerate
endmodule
