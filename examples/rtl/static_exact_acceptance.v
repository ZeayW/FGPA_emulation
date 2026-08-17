// Small real-RTL acceptance design for the opt-in static exact cut flow.
//
// The 33-input next-state parity needs at least seven 6-input LUTs.  On the
// deliberately capacity-limited academic acceptance BoardDB the complete
// combinational cone cannot fit on one FPGA, while the exact-cut policy can
// split it legally.  This is a functional/physical acceptance fixture, not a
// QoR benchmark.
module static_exact_acceptance (
    input  wire        clk,
    input  wire        rst,
    input  wire [31:0] data,
    output reg         q
);
    always @(posedge clk) begin
        if (rst)
            q <= 1'b0;
        else
            q <= q ^ ^data;
    end
endmodule
