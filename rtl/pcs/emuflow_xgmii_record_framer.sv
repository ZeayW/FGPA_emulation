// Copyright 2026 EmuFlow contributors
// SPDX-License-Identifier: Apache-2.0
//
// Three-cycle proprietary record envelope transported over an IEEE 802.3
// 64b/66b PCS through its XGMII interface.  This is not an Ethernet MAC.
module emuflow_xgmii_record_framer (
    input  wire        clk,
    input  wire        reset,
    input  wire        record_valid,
    output wire        record_ready,
    input  wire [1:0]  record_kind,
    input  wire [15:0] record_sequence,
    input  wire [63:0] record_payload,
    output reg  [63:0] xgmii_txd,
    output reg  [7:0]  xgmii_txc
);
    localparam [1:0] STATE_IDLE = 2'd0;
    localparam [1:0] STATE_HEADER = 2'd1;
    localparam [1:0] STATE_BODY = 2'd2;
    localparam [1:0] STATE_TERM = 2'd3;

    localparam [7:0] XGMII_IDLE = 8'h07;
    localparam [7:0] XGMII_START = 8'hfb;
    localparam [7:0] XGMII_TERM = 8'hfd;
    localparam [7:0] PROTOCOL_MAGIC = 8'he1;
    localparam [7:0] PROTOCOL_VERSION = 8'h01;

    reg [1:0] state;
    reg [1:0] kind_reg;
    reg [15:0] sequence_reg;
    reg [63:0] payload_reg;

    function automatic [15:0] crc16_ccitt;
        input [87:0] value;
        integer bit_index;
        reg feedback;
        reg [15:0] crc;
        begin
            crc = 16'hffff;
            for (bit_index = 87; bit_index >= 0; bit_index = bit_index - 1) begin
                feedback = crc[15] ^ value[bit_index];
                crc = {crc[14:0], 1'b0};
                if (feedback)
                    crc = crc ^ 16'h1021;
            end
            crc16_ccitt = crc;
        end
    endfunction

    wire [15:0] record_crc = crc16_ccitt(
        {{6{1'b0}}, kind_reg, sequence_reg, payload_reg}
    );

    // A new record may be captured on the terminate cycle, sustaining one
    // complete record every three PCS clocks.
    assign record_ready = state == STATE_IDLE || state == STATE_TERM;

    always @* begin
        xgmii_txd = {8{XGMII_IDLE}};
        xgmii_txc = 8'hff;
        case (state)
            STATE_HEADER: begin
                xgmii_txd = {
                    payload_reg[15:8],
                    payload_reg[7:0],
                    sequence_reg[15:8],
                    sequence_reg[7:0],
                    {6'b0, kind_reg},
                    PROTOCOL_VERSION,
                    PROTOCOL_MAGIC,
                    XGMII_START
                };
                xgmii_txc = 8'b00000001;
            end
            STATE_BODY: begin
                xgmii_txd = {
                    record_crc[15:8],
                    record_crc[7:0],
                    payload_reg[63:56],
                    payload_reg[55:48],
                    payload_reg[47:40],
                    payload_reg[39:32],
                    payload_reg[31:24],
                    payload_reg[23:16]
                };
                xgmii_txc = 8'h00;
            end
            STATE_TERM: begin
                xgmii_txd = {
                    XGMII_IDLE, XGMII_IDLE, XGMII_IDLE, XGMII_IDLE,
                    XGMII_IDLE, XGMII_IDLE, XGMII_IDLE, XGMII_TERM
                };
                xgmii_txc = 8'hff;
            end
            default: begin
                xgmii_txd = {8{XGMII_IDLE}};
                xgmii_txc = 8'hff;
            end
        endcase
    end

    always @(posedge clk) begin
        if (reset) begin
            state <= STATE_IDLE;
            kind_reg <= 2'b0;
            sequence_reg <= 16'b0;
            payload_reg <= 64'b0;
        end else begin
            case (state)
                STATE_IDLE: begin
                    if (record_valid) begin
                        kind_reg <= record_kind;
                        sequence_reg <= record_sequence;
                        payload_reg <= record_payload;
                        state <= STATE_HEADER;
                    end
                end
                STATE_HEADER: state <= STATE_BODY;
                STATE_BODY: state <= STATE_TERM;
                STATE_TERM: begin
                    if (record_valid) begin
                        kind_reg <= record_kind;
                        sequence_reg <= record_sequence;
                        payload_reg <= record_payload;
                        state <= STATE_HEADER;
                    end else begin
                        state <= STATE_IDLE;
                    end
                end
                default: state <= STATE_IDLE;
            endcase
        end
    end
endmodule
