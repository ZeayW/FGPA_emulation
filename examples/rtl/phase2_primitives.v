module phase2_primitives (
    input  wire       clk,
    input  wire       rst,
    output wire [3:0] q
);
    wire [3:0] next_q;

    (* DONT_TOUCH = "yes" *)
    LUT2 #(.INIT(4'h6)) \next_lut[0] (
        .I0(q[0]), .I1(1'b1), .O(next_q[0])
    );
    (* DONT_TOUCH = "yes" *)
    LUT2 #(.INIT(4'h6)) \next_lut[1] (
        .I0(q[1]), .I1(q[0]), .O(next_q[1])
    );
    (* DONT_TOUCH = "yes" *)
    LUT2 #(.INIT(4'h6)) \next_lut[2] (
        .I0(q[2]), .I1(q[1]), .O(next_q[2])
    );
    (* DONT_TOUCH = "yes" *)
    LUT2 #(.INIT(4'h6)) \next_lut[3] (
        .I0(q[3]), .I1(q[2]), .O(next_q[3])
    );

    (* DONT_TOUCH = "yes" *)
    FDRE \q_reg[0] (
        .C(clk), .CE(1'b1), .D(next_q[0]), .Q(q[0]), .R(rst)
    );
    (* DONT_TOUCH = "yes" *)
    FDRE \q_reg[1] (
        .C(clk), .CE(1'b1), .D(next_q[1]), .Q(q[1]), .R(rst)
    );
    (* DONT_TOUCH = "yes" *)
    FDRE \q_reg[2] (
        .C(clk), .CE(1'b1), .D(next_q[2]), .Q(q[2]), .R(rst)
    );
    (* DONT_TOUCH = "yes" *)
    FDRE \q_reg[3] (
        .C(clk), .CE(1'b1), .D(next_q[3]), .Q(q[3]), .R(rst)
    );
endmodule
