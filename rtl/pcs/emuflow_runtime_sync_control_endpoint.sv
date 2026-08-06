// Copyright 2026 EmuFlow contributors
// SPDX-License-Identifier: Apache-2.0
//
// One directed runtime-sync tree edge over the EmuFlow PCS record channel.
// ROLE_PARENT=1 receives READY and transmits START.  ROLE_PARENT=0 transmits
// READY and receives START.  Kind 3 is reserved for a future reset/fault word.
module emuflow_runtime_sync_control_endpoint #(
    parameter integer ROLE_PARENT = 0
) (
    input  wire        fabric_clk,
    input  wire        reset,
    input  wire        local_subtree_ready,
    input  wire        local_start_valid,
    input  wire [31:0] local_start_epoch,
    output reg         remote_subtree_ready,
    output reg         remote_start_valid,
    output reg  [31:0] remote_start_epoch,

    output reg         tx_control_valid,
    input  wire        tx_control_ready,
    output reg  [1:0]  tx_control_kind,
    output reg  [15:0] tx_control_sequence,
    output reg  [63:0] tx_control_payload,
    input  wire        rx_control_valid,
    output wire        rx_control_ready,
    input  wire [1:0]  rx_control_kind,
    input  wire [15:0] rx_control_sequence,
    input  wire [63:0] rx_control_payload,
    output reg         protocol_error
);
    localparam [1:0] KIND_READY = 2'b01;
    localparam [1:0] KIND_START = 2'b10;
    localparam [1:0] KIND_FAULT = 2'b11;

    reg [15:0] next_tx_sequence;
    reg [15:0] expected_rx_sequence;
    reg last_ready_sent;
    reg ready_sent_valid;
    reg start_sent;

    assign rx_control_ready = 1'b1;

    always @(posedge fabric_clk) begin
        if (reset) begin
            remote_subtree_ready <= 1'b0;
            remote_start_valid <= 1'b0;
            remote_start_epoch <= 32'b0;
            tx_control_valid <= 1'b0;
            tx_control_kind <= KIND_READY;
            tx_control_sequence <= 16'b0;
            tx_control_payload <= 64'b0;
            next_tx_sequence <= 16'b0;
            expected_rx_sequence <= 16'b0;
            last_ready_sent <= 1'b0;
            ready_sent_valid <= 1'b0;
            start_sent <= 1'b0;
            protocol_error <= 1'b0;
        end else begin
            remote_start_valid <= 1'b0;

            if (tx_control_valid && tx_control_ready)
                tx_control_valid <= 1'b0;

            if (ROLE_PARENT != 0) begin
                if (!local_start_valid)
                    start_sent <= 1'b0;
                if (local_start_valid && !start_sent &&
                    (!tx_control_valid || tx_control_ready)) begin
                    tx_control_valid <= 1'b1;
                    tx_control_kind <= KIND_START;
                    tx_control_sequence <= next_tx_sequence;
                    tx_control_payload <= {32'b0, local_start_epoch};
                    next_tx_sequence <= next_tx_sequence + 1'b1;
                    start_sent <= 1'b1;
                end
            end else begin
                if ((!ready_sent_valid ||
                     local_subtree_ready != last_ready_sent) &&
                    (!tx_control_valid || tx_control_ready)) begin
                    tx_control_valid <= 1'b1;
                    tx_control_kind <= KIND_READY;
                    tx_control_sequence <= next_tx_sequence;
                    tx_control_payload <= {63'b0, local_subtree_ready};
                    next_tx_sequence <= next_tx_sequence + 1'b1;
                    last_ready_sent <= local_subtree_ready;
                    ready_sent_valid <= 1'b1;
                end
            end

            if (rx_control_valid) begin
                if (rx_control_sequence != expected_rx_sequence)
                    protocol_error <= 1'b1;
                expected_rx_sequence <= rx_control_sequence + 1'b1;
                if (ROLE_PARENT != 0) begin
                    if (rx_control_kind == KIND_READY) begin
                        remote_subtree_ready <= rx_control_payload[0];
                    end else if (rx_control_kind == KIND_FAULT) begin
                        remote_subtree_ready <= 1'b0;
                        protocol_error <= 1'b1;
                    end else begin
                        protocol_error <= 1'b1;
                    end
                end else begin
                    if (rx_control_kind == KIND_START) begin
                        remote_start_valid <= 1'b1;
                        remote_start_epoch <= rx_control_payload[31:0];
                    end else if (rx_control_kind == KIND_FAULT) begin
                        protocol_error <= 1'b1;
                    end else begin
                        protocol_error <= 1'b1;
                    end
                end
            end
        end
    end
endmodule
