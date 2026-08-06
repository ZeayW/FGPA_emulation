// Copyright (c) 2021 Alex Forencich
// Copyright 2026 EmuFlow contributors
// SPDX-License-Identifier: MIT
//
// Source-visible EmuFlow contract adapter around AMD GT Wizard products.
// The generated emuflow_gty_10g_full/channel modules are proprietary build
// artifacts and are deliberately not stored in this repository.

`timescale 1ns / 1ps
`default_nettype none

module emuflow_external_serial_clock_reset #(
    parameter integer BOARD_RESET_ACTIVE_LOW = 1
) (
    input  wire refclk_p,
    input  wire refclk_n,
    input  wire board_reset,
    output wire phy_refclk,
    output wire phy_reset,
    output wire ready
);
    wire reset_async = BOARD_RESET_ACTIVE_LOW ? !board_reset : board_reset;
    reg [3:0] reset_sync = 4'hf;

    IBUFDS_GTE4 refclk_input (
        .I(refclk_p),
        .IB(refclk_n),
        .CEB(1'b0),
        .O(phy_refclk),
        .ODIV2()
    );

    always @(posedge phy_refclk or posedge reset_async) begin
        if (reset_async)
            reset_sync <= 4'hf;
        else
            reset_sync <= {1'b0, reset_sync[3:1]};
    end

    assign phy_reset = reset_sync[0];
    assign ready = !reset_sync[0];
endmodule

module emuflow_vivado_gty_10g_channel_adapter #(
    parameter integer ACTIVE = 0,
    parameter integer HAS_COMMON = 0
) (
    input  wire        phy_refclk,
    input  wire        phy_reset,
    input  wire [63:0] serdes_tx_data,
    input  wire [1:0]  serdes_tx_hdr,
    output wire [63:0] serdes_rx_data,
    output wire [1:0]  serdes_rx_hdr,
    input  wire        serdes_rx_bitslip,
    input  wire        serdes_rx_reset_req,
    output wire        tx_usrclk,
    output wire        rx_usrclk,
    output wire        txp,
    output wire        txn,
    input  wire        rxp,
    input  wire        rxn,
    output wire        lane_ready,
    output wire        common_ready,
    input  wire        shared_qpll_lock,
    input  wire        shared_qpll_clk,
    input  wire        shared_qpll_refclk,
    output wire        owned_qpll_lock,
    output wire        owned_qpll_clk,
    output wire        owned_qpll_refclk
);
    generate
        if (!ACTIVE) begin : inactive
            assign serdes_rx_data = 64'b0;
            assign serdes_rx_hdr = 2'b0;
            assign tx_usrclk = 1'b0;
            assign rx_usrclk = 1'b0;
            assign txp = 1'b0;
            assign txn = 1'b0;
            assign lane_ready = 1'b0;
            assign common_ready = 1'b0;
            assign owned_qpll_lock = 1'b0;
            assign owned_qpll_clk = 1'b0;
            assign owned_qpll_refclk = 1'b0;
        end else if (HAS_COMMON) begin : common_owner
            wire gt_tx_done;
            wire gt_rx_done;
            wire gt_power_good;
            wire [5:0] gt_rx_hdr;
            wire [1:0] gt_rx_data_valid;
            wire [1:0] gt_rx_hdr_valid;

            emuflow_gty_10g_full gt_ip (
                .gtwiz_userclk_tx_reset_in(1'b0),
                .gtwiz_userclk_tx_srcclk_out(),
                .gtwiz_userclk_tx_usrclk_out(),
                .gtwiz_userclk_tx_usrclk2_out(tx_usrclk),
                .gtwiz_userclk_tx_active_out(),
                .gtwiz_userclk_rx_reset_in(1'b0),
                .gtwiz_userclk_rx_srcclk_out(),
                .gtwiz_userclk_rx_usrclk_out(),
                .gtwiz_userclk_rx_usrclk2_out(rx_usrclk),
                .gtwiz_userclk_rx_active_out(),
                .gtwiz_reset_clk_freerun_in(phy_refclk),
                .gtwiz_reset_all_in(phy_reset),
                .gtwiz_reset_tx_pll_and_datapath_in(1'b0),
                .gtwiz_reset_tx_datapath_in(1'b0),
                .gtwiz_reset_rx_pll_and_datapath_in(1'b0),
                .gtwiz_reset_rx_datapath_in(serdes_rx_reset_req),
                .gtwiz_reset_rx_cdr_stable_out(),
                .gtwiz_reset_tx_done_out(gt_tx_done),
                .gtwiz_reset_rx_done_out(gt_rx_done),
                .gtwiz_userdata_tx_in(serdes_tx_data),
                .gtwiz_userdata_rx_out(serdes_rx_data),
                .gtrefclk00_in(phy_refclk),
                .qpll0lock_out(owned_qpll_lock),
                .qpll0outclk_out(owned_qpll_clk),
                .qpll0outrefclk_out(owned_qpll_refclk),
                .gtyrxn_in(rxn), .gtyrxp_in(rxp),
                .rxgearboxslip_in(serdes_rx_bitslip),
                .txheader_in({4'b0, serdes_tx_hdr}),
                .txsequence_in(7'b0),
                .gtpowergood_out(gt_power_good),
                .gtytxn_out(txn), .gtytxp_out(txp),
                .rxdatavalid_out(gt_rx_data_valid),
                .rxheader_out(gt_rx_hdr),
                .rxheadervalid_out(gt_rx_hdr_valid),
                .rxpmaresetdone_out(), .rxprgdivresetdone_out(),
                .rxstartofseq_out(),
                .txpmaresetdone_out(), .txprgdivresetdone_out()
            );

            assign serdes_rx_hdr = gt_rx_hdr[1:0];
            assign lane_ready = gt_power_good && gt_tx_done && gt_rx_done;
            assign common_ready = gt_power_good && owned_qpll_lock;
        end else begin : shared_channel
            wire gt_tx_done;
            wire gt_rx_done;
            wire gt_power_good;
            wire unused_qpll_reset;
            wire [5:0] gt_rx_hdr;
            wire [1:0] gt_rx_data_valid;
            wire [1:0] gt_rx_hdr_valid;

            emuflow_gty_10g_channel gt_ip (
                .gtwiz_userclk_tx_reset_in(1'b0),
                .gtwiz_userclk_tx_srcclk_out(),
                .gtwiz_userclk_tx_usrclk_out(),
                .gtwiz_userclk_tx_usrclk2_out(tx_usrclk),
                .gtwiz_userclk_tx_active_out(),
                .gtwiz_userclk_rx_reset_in(1'b0),
                .gtwiz_userclk_rx_srcclk_out(),
                .gtwiz_userclk_rx_usrclk_out(),
                .gtwiz_userclk_rx_usrclk2_out(rx_usrclk),
                .gtwiz_userclk_rx_active_out(),
                .gtwiz_reset_clk_freerun_in(phy_refclk),
                .gtwiz_reset_all_in(phy_reset),
                .gtwiz_reset_tx_pll_and_datapath_in(1'b0),
                .gtwiz_reset_tx_datapath_in(1'b0),
                .gtwiz_reset_rx_pll_and_datapath_in(1'b0),
                .gtwiz_reset_rx_datapath_in(serdes_rx_reset_req),
                .gtwiz_reset_qpll0lock_in(shared_qpll_lock),
                .gtwiz_reset_rx_cdr_stable_out(),
                .gtwiz_reset_tx_done_out(gt_tx_done),
                .gtwiz_reset_rx_done_out(gt_rx_done),
                .gtwiz_reset_qpll0reset_out(unused_qpll_reset),
                .gtwiz_userdata_tx_in(serdes_tx_data),
                .gtwiz_userdata_rx_out(serdes_rx_data),
                .gtyrxn_in(rxn), .gtyrxp_in(rxp),
                .qpll0clk_in(shared_qpll_clk),
                .qpll0refclk_in(shared_qpll_refclk),
                .qpll1clk_in(1'b0), .qpll1refclk_in(1'b0),
                .rxgearboxslip_in(serdes_rx_bitslip),
                .txheader_in({4'b0, serdes_tx_hdr}),
                .txsequence_in(7'b0),
                .gtpowergood_out(gt_power_good),
                .gtytxn_out(txn), .gtytxp_out(txp),
                .rxdatavalid_out(gt_rx_data_valid),
                .rxheader_out(gt_rx_hdr),
                .rxheadervalid_out(gt_rx_hdr_valid),
                .rxpmaresetdone_out(), .rxprgdivresetdone_out(),
                .rxstartofseq_out(),
                .txpmaresetdone_out(), .txprgdivresetdone_out()
            );

            assign serdes_rx_hdr = gt_rx_hdr[1:0];
            assign lane_ready = gt_power_good && gt_tx_done && gt_rx_done;
            assign common_ready = 1'b0;
            assign owned_qpll_lock = 1'b0;
            assign owned_qpll_clk = 1'b0;
            assign owned_qpll_refclk = 1'b0;
        end
    endgenerate
endmodule

module emuflow_external_gty_serdes_quad #(
    parameter [3:0] ACTIVE_CHANNEL_MASK = 4'b0000
) (
    input  wire         phy_refclk,
    input  wire         phy_reset,
    input  wire [255:0] serdes_tx_data,
    input  wire [7:0]   serdes_tx_hdr,
    output wire [255:0] serdes_rx_data,
    output wire [7:0]   serdes_rx_hdr,
    input  wire [3:0]   serdes_rx_bitslip,
    input  wire [3:0]   serdes_rx_reset_req,
    output wire [3:0]   tx_usrclk,
    output wire [3:0]   rx_usrclk,
    output wire [3:0]   txp,
    output wire [3:0]   txn,
    input  wire [3:0]   rxp,
    input  wire [3:0]   rxn,
    output wire [3:0]   lane_ready,
    output wire         common_ready
);
    localparam integer COMMON_CHANNEL = ACTIVE_CHANNEL_MASK[0] ? 0 :
                                        ACTIVE_CHANNEL_MASK[1] ? 1 :
                                        ACTIVE_CHANNEL_MASK[2] ? 2 : 3;
    wire [3:0] owned_qpll_lock;
    wire [3:0] owned_qpll_clk;
    wire [3:0] owned_qpll_refclk;
    wire shared_qpll_lock = owned_qpll_lock[COMMON_CHANNEL];
    wire shared_qpll_clk = owned_qpll_clk[COMMON_CHANNEL];
    wire shared_qpll_refclk = owned_qpll_refclk[COMMON_CHANNEL];
    wire [3:0] channel_common_ready;

    initial begin
        if (ACTIVE_CHANNEL_MASK == 4'b0000)
            $error("EmuFlow GTY quad requires at least one active channel");
    end

    genvar channel;
    generate
        for (channel = 0; channel < 4; channel = channel + 1) begin : channel_gen
            emuflow_vivado_gty_10g_channel_adapter #(
                .ACTIVE(ACTIVE_CHANNEL_MASK[channel]),
                .HAS_COMMON(channel == COMMON_CHANNEL)
            ) adapter (
                .phy_refclk(phy_refclk), .phy_reset(phy_reset),
                .serdes_tx_data(serdes_tx_data[channel*64 +: 64]),
                .serdes_tx_hdr(serdes_tx_hdr[channel*2 +: 2]),
                .serdes_rx_data(serdes_rx_data[channel*64 +: 64]),
                .serdes_rx_hdr(serdes_rx_hdr[channel*2 +: 2]),
                .serdes_rx_bitslip(serdes_rx_bitslip[channel]),
                .serdes_rx_reset_req(serdes_rx_reset_req[channel]),
                .tx_usrclk(tx_usrclk[channel]),
                .rx_usrclk(rx_usrclk[channel]),
                .txp(txp[channel]), .txn(txn[channel]),
                .rxp(rxp[channel]), .rxn(rxn[channel]),
                .lane_ready(lane_ready[channel]),
                .common_ready(channel_common_ready[channel]),
                .shared_qpll_lock(shared_qpll_lock),
                .shared_qpll_clk(shared_qpll_clk),
                .shared_qpll_refclk(shared_qpll_refclk),
                .owned_qpll_lock(owned_qpll_lock[channel]),
                .owned_qpll_clk(owned_qpll_clk[channel]),
                .owned_qpll_refclk(owned_qpll_refclk[channel])
            );
        end
    endgenerate

    assign common_ready = |channel_common_ready;
endmodule

`default_nettype wire
