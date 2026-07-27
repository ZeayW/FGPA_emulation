// SPDX-License-Identifier: Apache-2.0
//
// Technology-map physical-only UltraScale+ carry and wide-mux primitives
// back into ordinary LUT primitives. This policy is intended for
// board-independent emulation-flow validation before carry-chain-aware
// placement is enabled.

module GND (
  output G
);
  assign G = 1'b0;
endmodule

module VCC (
  output P
);
  assign P = 1'b1;
endmodule

module MUXF7 (
  output O,
  input I0,
  input I1,
  input S
);
  LUT3 #(.INIT(8'hca)) soft_mux (
    .I0(I0),
    .I1(I1),
    .I2(S),
    .O(O)
  );
endmodule

module MUXF8 (
  output O,
  input I0,
  input I1,
  input S
);
  LUT3 #(.INIT(8'hca)) soft_mux (
    .I0(I0),
    .I1(I1),
    .I2(S),
    .O(O)
  );
endmodule

module CARRY8 (
  output [7:0] CO,
  output [7:0] O,
  input CI,
  input CI_TOP,
  input [7:0] DI,
  input [7:0] S
);
  parameter CARRY_TYPE = "SINGLE_CY8";

  wire carry4 = CARRY_TYPE == "DUAL_CY4" ? CI_TOP : CO[3];

  LUT2 #(.INIT(4'h6)) sum0 (.I0(S[0]), .I1(CI), .O(O[0]));
  LUT3 #(.INIT(8'hca)) carry0 (
    .I0(DI[0]), .I1(CI), .I2(S[0]), .O(CO[0])
  );
  LUT2 #(.INIT(4'h6)) sum1 (.I0(S[1]), .I1(CO[0]), .O(O[1]));
  LUT3 #(.INIT(8'hca)) carry1 (
    .I0(DI[1]), .I1(CO[0]), .I2(S[1]), .O(CO[1])
  );
  LUT2 #(.INIT(4'h6)) sum2 (.I0(S[2]), .I1(CO[1]), .O(O[2]));
  LUT3 #(.INIT(8'hca)) carry2 (
    .I0(DI[2]), .I1(CO[1]), .I2(S[2]), .O(CO[2])
  );
  LUT2 #(.INIT(4'h6)) sum3 (.I0(S[3]), .I1(CO[2]), .O(O[3]));
  LUT3 #(.INIT(8'hca)) carry3 (
    .I0(DI[3]), .I1(CO[2]), .I2(S[3]), .O(CO[3])
  );
  LUT2 #(.INIT(4'h6)) sum4 (.I0(S[4]), .I1(carry4), .O(O[4]));
  LUT3 #(.INIT(8'hca)) carry4_lut (
    .I0(DI[4]), .I1(carry4), .I2(S[4]), .O(CO[4])
  );
  LUT2 #(.INIT(4'h6)) sum5 (.I0(S[5]), .I1(CO[4]), .O(O[5]));
  LUT3 #(.INIT(8'hca)) carry5 (
    .I0(DI[5]), .I1(CO[4]), .I2(S[5]), .O(CO[5])
  );
  LUT2 #(.INIT(4'h6)) sum6 (.I0(S[6]), .I1(CO[5]), .O(O[6]));
  LUT3 #(.INIT(8'hca)) carry6 (
    .I0(DI[6]), .I1(CO[5]), .I2(S[6]), .O(CO[6])
  );
  LUT2 #(.INIT(4'h6)) sum7 (.I0(S[7]), .I1(CO[6]), .O(O[7]));
  LUT3 #(.INIT(8'hca)) carry7 (
    .I0(DI[7]), .I1(CO[6]), .I2(S[7]), .O(CO[7])
  );
endmodule
