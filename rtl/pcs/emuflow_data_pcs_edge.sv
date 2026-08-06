// Copyright 2026 EmuFlow contributors
// SPDX-License-Identifier: Apache-2.0
module emuflow_data_pcs_edge #(
    parameter integer FIFO_DEPTH = 32,
    parameter integer DEJITTER_THRESHOLD = 8,
    parameter BIT_REVERSE = 0,
    parameter COUNT_125US = 19531
) (
    input  wire        fabric_clk,
    input  wire        fabric_reset,
    input  wire        data_tx_valid,
    output wire        data_tx_ready,
    input  wire [15:0] data_tx_sequence,
    input  wire [63:0] data_tx_payload,
    output wire        data_rx_valid,
    input  wire        data_rx_ready,
    output wire [15:0] data_rx_sequence,
    output wire [63:0] data_rx_payload,
    input  wire        pcs_tx_clk,
    input  wire        pcs_tx_reset,
    input  wire        pcs_rx_clk,
    input  wire        pcs_rx_reset,
    output wire [63:0] serdes_tx_data,
    output wire [1:0]  serdes_tx_hdr,
    input  wire [63:0] serdes_rx_data,
    input  wire [1:0]  serdes_rx_hdr,
    output wire        serdes_rx_bitslip,
    output wire        serdes_rx_reset_req,
    output wire        link_ready,
    output wire        release_started,
    output wire        edge_error
);
    wire elastic_valid;
    wire elastic_ready;
    wire [1:0] elastic_kind;
    wire [15:0] elastic_sequence;
    wire [63:0] elastic_payload;
    wire adapter_error;
    wire tx_fifo_overflow;
    wire rx_fifo_overflow;
    wire dejitter_overflow;
    wire dejitter_underflow;
    wire dejitter_sequence_error;
    wire dejitter_input_ready;
    reg kind_error;

    emuflow_10g_pcs_cdc_adapter #(
        .FIFO_DEPTH(FIFO_DEPTH), .BIT_REVERSE(BIT_REVERSE),
        .COUNT_125US(COUNT_125US)
    ) cdc_adapter (
        .fabric_clk(fabric_clk), .fabric_reset(fabric_reset),
        .tx_record_valid(data_tx_valid), .tx_record_ready(data_tx_ready),
        .tx_record_kind(2'b00), .tx_record_sequence(data_tx_sequence),
        .tx_record_payload(data_tx_payload),
        .tx_fifo_overflow(tx_fifo_overflow),
        .rx_record_valid(elastic_valid), .rx_record_ready(elastic_ready),
        .rx_record_kind(elastic_kind),
        .rx_record_sequence(elastic_sequence),
        .rx_record_payload(elastic_payload),
        .rx_fifo_overflow(rx_fifo_overflow),
        .pcs_tx_clk(pcs_tx_clk), .pcs_tx_reset(pcs_tx_reset),
        .pcs_rx_clk(pcs_rx_clk), .pcs_rx_reset(pcs_rx_reset),
        .serdes_tx_data(serdes_tx_data), .serdes_tx_hdr(serdes_tx_hdr),
        .serdes_rx_data(serdes_rx_data), .serdes_rx_hdr(serdes_rx_hdr),
        .serdes_rx_bitslip(serdes_rx_bitslip),
        .serdes_rx_reset_req(serdes_rx_reset_req),
        .link_ready(link_ready), .link_error(adapter_error)
    );

    assign elastic_ready =
        elastic_kind == 2'b00 ? dejitter_input_ready : 1'b1;
    always @(posedge fabric_clk) begin
        if (fabric_reset)
            kind_error <= 1'b0;
        else if (elastic_valid && elastic_ready && elastic_kind != 2'b00)
            kind_error <= 1'b1;
    end

    emuflow_record_dejitter_buffer #(
        .DEPTH(FIFO_DEPTH), .START_THRESHOLD(DEJITTER_THRESHOLD)
    ) dejitter (
        .fabric_clk(fabric_clk), .reset(fabric_reset),
        .input_valid(elastic_valid && elastic_kind == 2'b00),
        .input_ready(dejitter_input_ready),
        .input_sequence(elastic_sequence), .input_payload(elastic_payload),
        .output_valid(data_rx_valid), .output_ready(data_rx_ready),
        .output_sequence(data_rx_sequence),
        .output_payload(data_rx_payload),
        .release_started(release_started),
        .overflow_error(dejitter_overflow),
        .underflow_error(dejitter_underflow),
        .sequence_error(dejitter_sequence_error)
    );

    assign edge_error =
        adapter_error || tx_fifo_overflow || rx_fifo_overflow || kind_error ||
        dejitter_overflow || dejitter_underflow || dejitter_sequence_error;
endmodule
