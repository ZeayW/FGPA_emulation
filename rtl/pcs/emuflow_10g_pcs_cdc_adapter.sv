// Copyright 2026 EmuFlow contributors
// SPDX-License-Identifier: Apache-2.0
//
// Bidirectional record CDC around the source-visible 10GBASE-R PCS.  This
// adapter preserves records and ordering; deterministic fabric-slot release
// is performed by the downstream sequence/de-jitter stage.
module emuflow_10g_pcs_cdc_adapter #(
    parameter integer FIFO_DEPTH = 32,
    parameter BIT_REVERSE = 0,
    parameter COUNT_125US = 19531
) (
    input  wire        fabric_clk,
    input  wire        fabric_reset,
    input  wire        tx_record_valid,
    output wire        tx_record_ready,
    input  wire [1:0]  tx_record_kind,
    input  wire [15:0] tx_record_sequence,
    input  wire [63:0] tx_record_payload,
    output wire        tx_fifo_overflow,

    output wire        rx_record_valid,
    input  wire        rx_record_ready,
    output wire [1:0]  rx_record_kind,
    output wire [15:0] rx_record_sequence,
    output wire [63:0] rx_record_payload,
    output wire        rx_fifo_overflow,

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
    output wire        link_error
);
    wire pcs_tx_valid;
    wire pcs_tx_ready;
    wire [1:0] pcs_tx_kind;
    wire [15:0] pcs_tx_sequence;
    wire [63:0] pcs_tx_payload;
    wire pcs_rx_valid;
    wire pcs_rx_ready;
    wire [1:0] pcs_rx_kind;
    wire [15:0] pcs_rx_sequence;
    wire [63:0] pcs_rx_payload;
    wire tx_bad_block;
    wire [6:0] rx_error_count;
    wire rx_bad_block;
    wire rx_sequence_error;
    wire rx_block_lock;
    wire rx_high_ber;
    wire rx_status;
    wire record_framing_error;
    wire record_crc_error;
    wire record_overflow_error;
    wire pcs_link_ready = rx_status && rx_block_lock && !rx_high_ber;
    reg pcs_rx_error_sticky;
    reg pcs_tx_error_sticky;
    reg link_ready_sync1;
    reg link_ready_sync2;
    reg rx_error_sync1;
    reg rx_error_sync2;
    reg tx_error_sync1;
    reg tx_error_sync2;

    emuflow_record_async_fifo #(.DEPTH(FIFO_DEPTH)) tx_fifo (
        .source_clk(fabric_clk), .source_reset(fabric_reset),
        .source_valid(tx_record_valid), .source_ready(tx_record_ready),
        .source_kind(tx_record_kind),
        .source_sequence(tx_record_sequence),
        .source_payload(tx_record_payload),
        .source_overflow(tx_fifo_overflow),
        .sink_clk(pcs_tx_clk), .sink_reset(pcs_tx_reset),
        .sink_valid(pcs_tx_valid), .sink_ready(pcs_tx_ready),
        .sink_kind(pcs_tx_kind), .sink_sequence(pcs_tx_sequence),
        .sink_payload(pcs_tx_payload)
    );

    emuflow_10g_pcs_record_link #(
        .BIT_REVERSE(BIT_REVERSE), .COUNT_125US(COUNT_125US)
    ) pcs_link (
        .tx_clk(pcs_tx_clk), .tx_reset(pcs_tx_reset),
        .tx_record_valid(pcs_tx_valid), .tx_record_ready(pcs_tx_ready),
        .tx_record_kind(pcs_tx_kind),
        .tx_record_sequence(pcs_tx_sequence),
        .tx_record_payload(pcs_tx_payload),
        .rx_clk(pcs_rx_clk), .rx_reset(pcs_rx_reset),
        .rx_record_valid(pcs_rx_valid), .rx_record_ready(pcs_rx_ready),
        .rx_record_kind(pcs_rx_kind),
        .rx_record_sequence(pcs_rx_sequence),
        .rx_record_payload(pcs_rx_payload),
        .serdes_tx_data(serdes_tx_data), .serdes_tx_hdr(serdes_tx_hdr),
        .serdes_rx_data(serdes_rx_data), .serdes_rx_hdr(serdes_rx_hdr),
        .serdes_rx_bitslip(serdes_rx_bitslip),
        .serdes_rx_reset_req(serdes_rx_reset_req),
        .tx_bad_block(tx_bad_block), .rx_error_count(rx_error_count),
        .rx_bad_block(rx_bad_block),
        .rx_sequence_error(rx_sequence_error),
        .rx_block_lock(rx_block_lock), .rx_high_ber(rx_high_ber),
        .rx_status(rx_status),
        .record_framing_error(record_framing_error),
        .record_crc_error(record_crc_error),
        .record_overflow_error(record_overflow_error)
    );

    emuflow_record_async_fifo #(.DEPTH(FIFO_DEPTH)) rx_fifo (
        .source_clk(pcs_rx_clk), .source_reset(pcs_rx_reset),
        .source_valid(pcs_rx_valid), .source_ready(pcs_rx_ready),
        .source_kind(pcs_rx_kind),
        .source_sequence(pcs_rx_sequence),
        .source_payload(pcs_rx_payload),
        .source_overflow(rx_fifo_overflow),
        .sink_clk(fabric_clk), .sink_reset(fabric_reset),
        .sink_valid(rx_record_valid), .sink_ready(rx_record_ready),
        .sink_kind(rx_record_kind), .sink_sequence(rx_record_sequence),
        .sink_payload(rx_record_payload)
    );

    always @(posedge pcs_rx_clk) begin
        if (pcs_rx_reset) begin
            pcs_rx_error_sticky <= 1'b0;
        end else if (rx_bad_block || rx_sequence_error || rx_high_ber ||
                     record_framing_error || record_crc_error ||
                     record_overflow_error || rx_fifo_overflow) begin
            pcs_rx_error_sticky <= 1'b1;
        end
    end
    always @(posedge pcs_tx_clk) begin
        if (pcs_tx_reset)
            pcs_tx_error_sticky <= 1'b0;
        else if (tx_bad_block)
            pcs_tx_error_sticky <= 1'b1;
    end
    always @(posedge fabric_clk) begin
        if (fabric_reset) begin
            link_ready_sync1 <= 1'b0;
            link_ready_sync2 <= 1'b0;
            rx_error_sync1 <= 1'b0;
            rx_error_sync2 <= 1'b0;
            tx_error_sync1 <= 1'b0;
            tx_error_sync2 <= 1'b0;
        end else begin
            link_ready_sync1 <= pcs_link_ready;
            link_ready_sync2 <= link_ready_sync1;
            rx_error_sync1 <= pcs_rx_error_sticky;
            rx_error_sync2 <= rx_error_sync1;
            tx_error_sync1 <= pcs_tx_error_sticky;
            tx_error_sync2 <= tx_error_sync1;
        end
    end

    assign link_ready = link_ready_sync2;
    assign link_error =
        tx_fifo_overflow || rx_error_sync2 || tx_error_sync2;
endmodule
