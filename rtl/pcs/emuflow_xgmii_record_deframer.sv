// Copyright 2026 EmuFlow contributors
// SPDX-License-Identifier: Apache-2.0
module emuflow_xgmii_record_deframer (
    input  wire        clk,
    input  wire        reset,
    input  wire [63:0] xgmii_rxd,
    input  wire [7:0]  xgmii_rxc,
    output reg         record_valid,
    input  wire        record_ready,
    output reg  [1:0]  record_kind,
    output reg  [15:0] record_sequence,
    output reg  [63:0] record_payload,
    output reg         framing_error,
    output reg         crc_error,
    output reg         overflow_error
);
    localparam [1:0] STATE_IDLE = 2'd0;
    localparam [1:0] STATE_BODY = 2'd1;
    localparam [1:0] STATE_TERM = 2'd2;

    localparam [7:0] XGMII_IDLE = 8'h07;
    localparam [7:0] XGMII_START = 8'hfb;
    localparam [7:0] XGMII_TERM = 8'hfd;
    localparam [7:0] PROTOCOL_MAGIC = 8'he1;
    localparam [7:0] PROTOCOL_VERSION = 8'h01;

    reg [1:0] state;
    reg [1:0] kind_reg;
    reg [15:0] sequence_reg;
    reg [15:0] payload_low_reg;
    reg [63:0] payload_reg;
    reg body_crc_ok;

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

    wire header_valid =
        xgmii_rxc == 8'b00000001 &&
        xgmii_rxd[7:0] == XGMII_START &&
        xgmii_rxd[15:8] == PROTOCOL_MAGIC &&
        xgmii_rxd[23:16] == PROTOCOL_VERSION &&
        xgmii_rxd[31:26] == 6'b0;
    wire term_valid =
        xgmii_rxc == 8'hff &&
        xgmii_rxd[7:0] == XGMII_TERM &&
        xgmii_rxd[63:8] == {7{XGMII_IDLE}};
    wire [63:0] assembled_payload = {
        xgmii_rxd[47:40], xgmii_rxd[39:32], xgmii_rxd[31:24],
        xgmii_rxd[23:16], xgmii_rxd[15:8], xgmii_rxd[7:0],
        payload_low_reg[15:8], payload_low_reg[7:0]
    };
    wire [15:0] received_crc = {xgmii_rxd[63:56], xgmii_rxd[55:48]};
    wire [15:0] expected_crc = crc16_ccitt(
        {{6{1'b0}}, kind_reg, sequence_reg, assembled_payload}
    );

    always @(posedge clk) begin
        if (reset) begin
            state <= STATE_IDLE;
            kind_reg <= 2'b0;
            sequence_reg <= 16'b0;
            payload_low_reg <= 16'b0;
            payload_reg <= 64'b0;
            body_crc_ok <= 1'b0;
            record_valid <= 1'b0;
            record_kind <= 2'b0;
            record_sequence <= 16'b0;
            record_payload <= 64'b0;
            framing_error <= 1'b0;
            crc_error <= 1'b0;
            overflow_error <= 1'b0;
        end else begin
            if (record_valid && record_ready)
                record_valid <= 1'b0;
            case (state)
                STATE_IDLE: begin
                    if (header_valid) begin
                        kind_reg <= xgmii_rxd[25:24];
                        sequence_reg <= {xgmii_rxd[47:40], xgmii_rxd[39:32]};
                        payload_low_reg <= {xgmii_rxd[63:56], xgmii_rxd[55:48]};
                        state <= STATE_BODY;
                    end else if (xgmii_rxc != 8'hff ||
                                 xgmii_rxd != {8{XGMII_IDLE}}) begin
                        framing_error <= 1'b1;
                    end
                end
                STATE_BODY: begin
                    if (xgmii_rxc != 8'h00) begin
                        framing_error <= 1'b1;
                        state <= STATE_IDLE;
                    end else begin
                        payload_reg <= assembled_payload;
                        body_crc_ok <= received_crc == expected_crc;
                        if (received_crc != expected_crc)
                            crc_error <= 1'b1;
                        state <= STATE_TERM;
                    end
                end
                STATE_TERM: begin
                    if (!term_valid) begin
                        framing_error <= 1'b1;
                    end else if (body_crc_ok) begin
                        if (!record_valid || record_ready) begin
                            record_valid <= 1'b1;
                            record_kind <= kind_reg;
                            record_sequence <= sequence_reg;
                            record_payload <= payload_reg;
                        end else begin
                            overflow_error <= 1'b1;
                        end
                    end
                    state <= STATE_IDLE;
                end
                default: state <= STATE_IDLE;
            endcase
        end
    end
endmodule
