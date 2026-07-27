module xilinx_softlogic_smoke (
  input ci,
  input ci_top,
  input [7:0] di,
  input [7:0] s,
  output [7:0] co,
  output [7:0] o,
  output mux_o
);
  wire mux7_o;

  CARRY8 #(
    .CARRY_TYPE("DUAL_CY4")
  ) carry (
    .CI(ci),
    .CI_TOP(ci_top),
    .DI(di),
    .S(s),
    .CO(co),
    .O(o)
  );
  MUXF7 mux7 (
    .I0(o[0]),
    .I1(o[1]),
    .S(s[0]),
    .O(mux7_o)
  );
  MUXF8 mux8 (
    .I0(mux7_o),
    .I1(o[7]),
    .S(s[7]),
    .O(mux_o)
  );
endmodule
