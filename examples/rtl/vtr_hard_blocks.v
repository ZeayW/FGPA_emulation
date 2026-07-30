// Copyright 2026 EmuFlow contributors
// SPDX-License-Identifier: Apache-2.0

module vtr_hard_blocks (
    input  wire        clk,
    input  wire        we,
    input  wire [9:0]  addr,
    input  wire [31:0] data,
    input  wire [17:0] a,
    input  wire [17:0] b,
    output reg  [31:0] q,
    output wire [35:0] product,
    output wire [31:0] mixed
);
    reg [31:0] memory [0:1023];

    always @(posedge clk) begin
        if (we)
            memory[addr] <= data;
        q <= memory[addr];
    end

    assign product = a * b;
    assign mixed = q ^ product[31:0] ^ data;
endmodule

module vtr_dual_port_ram (
    input  wire        clk,
    input  wire        we1,
    input  wire        we2,
    input  wire [9:0]  addr1,
    input  wire [9:0]  addr2,
    input  wire [31:0] data1,
    input  wire [31:0] data2,
    output reg  [31:0] q1,
    output reg  [31:0] q2
);
    reg [31:0] memory [0:1023];

    always @(posedge clk) begin
        if (we1)
            memory[addr1] <= data1;
        q1 <= memory[addr1];
    end

    always @(posedge clk) begin
        if (we2)
            memory[addr2] <= data2;
        q2 <= memory[addr2];
    end
endmodule
