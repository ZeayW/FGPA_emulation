// SPDX-License-Identifier: Apache-2.0
//
// Minimal interface declarations for the UltraScale+ primitives retained by
// the NVDLA logic-only Vivado netlist.  Keeping this file intentionally small
// avoids loading simulation models (and their procedural behavior) into the
// Yosys JSON bridge.

(* blackbox *)
module LUT1 #(parameter [1:0] INIT = 2'h0) (
  input I0,
  output O
);
endmodule

(* blackbox *)
module LUT2 #(parameter [3:0] INIT = 4'h0) (
  input I0, I1,
  output O
);
endmodule

(* blackbox *)
module LUT3 #(parameter [7:0] INIT = 8'h00) (
  input I0, I1, I2,
  output O
);
endmodule

(* blackbox *)
module LUT4 #(parameter [15:0] INIT = 16'h0000) (
  input I0, I1, I2, I3,
  output O
);
endmodule

(* blackbox *)
module LUT5 #(parameter [31:0] INIT = 32'h00000000) (
  input I0, I1, I2, I3, I4,
  output O
);
endmodule

(* blackbox *)
module LUT6 #(parameter [63:0] INIT = 64'h0000000000000000) (
  input I0, I1, I2, I3, I4, I5,
  output O
);
endmodule

(* blackbox *)
module FDCE #(
  parameter INIT = 1'b0,
  parameter IS_C_INVERTED = 1'b0,
  parameter IS_CLR_INVERTED = 1'b0,
  parameter IS_D_INVERTED = 1'b0
) (
  input C, CE, CLR, D,
  output Q
);
endmodule

(* blackbox *)
module FDPE #(
  parameter INIT = 1'b0,
  parameter IS_C_INVERTED = 1'b0,
  parameter IS_PRE_INVERTED = 1'b0,
  parameter IS_D_INVERTED = 1'b0
) (
  input C, CE, PRE, D,
  output Q
);
endmodule

(* blackbox *)
module FDRE #(
  parameter INIT = 1'b0,
  parameter IS_C_INVERTED = 1'b0,
  parameter IS_R_INVERTED = 1'b0,
  parameter IS_D_INVERTED = 1'b0
) (
  input C, CE, R, D,
  output Q
);
endmodule

(* blackbox *)
module FDSE #(
  parameter INIT = 1'b0,
  parameter IS_C_INVERTED = 1'b0,
  parameter IS_S_INVERTED = 1'b0,
  parameter IS_D_INVERTED = 1'b0
) (
  input C, CE, S, D,
  output Q
);
endmodule

(* blackbox *)
module CARRY8 #(
  parameter CARRY_TYPE = "SINGLE_CY8"
) (
  input CI, CI_TOP,
  input [7:0] DI, S,
  output [7:0] CO, O
);
endmodule

(* blackbox *)
module MUXF7 (
  input I0, I1, S,
  output O
);
endmodule

(* blackbox *)
module MUXF8 (
  input I0, I1, S,
  output O
);
endmodule

(* blackbox *)
module GND (output G);
endmodule

(* blackbox *)
module VCC (output P);
endmodule
