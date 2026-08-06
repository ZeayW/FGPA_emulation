// Copyright 2026 EmuFlow contributors
// SPDX-License-Identifier: Apache-2.0
//
// Record interface around the Corundum 10GBASE-R PCS.  CDC and deterministic
// fabric-slot release are intentionally separate modules.
module emuflow_10g_pcs_record_link #(
    parameter BIT_REVERSE = 0,
    parameter COUNT_125US = 19531
) (
    input  wire        tx_clk,
    input  wire        tx_reset,
    input  wire        tx_record_valid,
    output wire        tx_record_ready,
    input  wire [1:0]  tx_record_kind,
    input  wire [15:0] tx_record_sequence,
    input  wire [63:0] tx_record_payload,

    input  wire        rx_clk,
    input  wire        rx_reset,
    output wire        rx_record_valid,
    input  wire        rx_record_ready,
    output wire [1:0]  rx_record_kind,
    output wire [15:0] rx_record_sequence,
    output wire [63:0] rx_record_payload,

    output wire [63:0] serdes_tx_data,
    output wire [1:0]  serdes_tx_hdr,
    input  wire [63:0] serdes_rx_data,
    input  wire [1:0]  serdes_rx_hdr,
    output wire        serdes_rx_bitslip,
    output wire        serdes_rx_reset_req,

    output wire        tx_bad_block,
    output wire [6:0]  rx_error_count,
    output wire        rx_bad_block,
    output wire        rx_sequence_error,
    output wire        rx_block_lock,
    output wire        rx_high_ber,
    output wire        rx_status,
    output wire        record_framing_error,
    output wire        record_crc_error,
    output wire        record_overflow_error
);
    wire [63:0] xgmii_txd;
    wire [7:0] xgmii_txc;
    wire [63:0] xgmii_rxd;
    wire [7:0] xgmii_rxc;

    emuflow_xgmii_record_framer record_framer (
        .clk(tx_clk), .reset(tx_reset),
        .record_valid(tx_record_valid), .record_ready(tx_record_ready),
        .record_kind(tx_record_kind),
        .record_sequence(tx_record_sequence),
        .record_payload(tx_record_payload),
        .xgmii_txd(xgmii_txd), .xgmii_txc(xgmii_txc)
    );

    eth_phy_10g #(
        .DATA_WIDTH(64), .CTRL_WIDTH(8), .HDR_WIDTH(2),
        .BIT_REVERSE(BIT_REVERSE), .COUNT_125US(COUNT_125US)
    ) pcs (
        .rx_clk(rx_clk), .rx_rst(rx_reset),
        .tx_clk(tx_clk), .tx_rst(tx_reset),
        .xgmii_txd(xgmii_txd), .xgmii_txc(xgmii_txc),
        .xgmii_rxd(xgmii_rxd), .xgmii_rxc(xgmii_rxc),
        .serdes_tx_data(serdes_tx_data), .serdes_tx_hdr(serdes_tx_hdr),
        .serdes_rx_data(serdes_rx_data), .serdes_rx_hdr(serdes_rx_hdr),
        .serdes_rx_bitslip(serdes_rx_bitslip),
        .serdes_rx_reset_req(serdes_rx_reset_req),
        .tx_bad_block(tx_bad_block), .rx_error_count(rx_error_count),
        .rx_bad_block(rx_bad_block),
        .rx_sequence_error(rx_sequence_error),
        .rx_block_lock(rx_block_lock), .rx_high_ber(rx_high_ber),
        .rx_status(rx_status),
        .cfg_tx_prbs31_enable(1'b0), .cfg_rx_prbs31_enable(1'b0)
    );

    emuflow_xgmii_record_deframer record_deframer (
        // Do not interpret alignment/training traffic as EmuFlow records.
        .clk(rx_clk), .reset(rx_reset || !rx_block_lock),
        .xgmii_rxd(xgmii_rxd), .xgmii_rxc(xgmii_rxc),
        .record_valid(rx_record_valid), .record_ready(rx_record_ready),
        .record_kind(rx_record_kind),
        .record_sequence(rx_record_sequence),
        .record_payload(rx_record_payload),
        .framing_error(record_framing_error),
        .crc_error(record_crc_error),
        .overflow_error(record_overflow_error)
    );
endmodule
