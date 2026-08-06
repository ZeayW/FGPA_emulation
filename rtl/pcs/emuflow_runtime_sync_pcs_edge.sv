// Copyright 2026 EmuFlow contributors
// SPDX-License-Identifier: Apache-2.0
//
// Complete source-visible record/control path for one full-duplex tree edge.
module emuflow_runtime_sync_pcs_edge #(
    parameter integer ROLE_PARENT = 0,
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

    input  wire        local_subtree_ready,
    input  wire        local_start_valid,
    input  wire [31:0] local_start_epoch,
    output wire        remote_subtree_ready,
    output wire        remote_start_valid,
    output wire [31:0] remote_start_epoch,

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
    wire control_tx_valid;
    wire control_tx_ready;
    wire [1:0] control_tx_kind;
    wire [15:0] control_tx_sequence;
    wire [63:0] control_tx_payload;
    wire control_rx_valid;
    wire control_rx_ready;
    wire [1:0] control_rx_kind;
    wire [15:0] control_rx_sequence;
    wire [63:0] control_rx_payload;
    wire mux_valid;
    wire mux_ready;
    wire [1:0] mux_kind;
    wire [15:0] mux_sequence;
    wire [63:0] mux_payload;
    wire adapter_rx_valid;
    wire adapter_rx_ready;
    wire [1:0] adapter_rx_kind;
    wire [15:0] adapter_rx_sequence;
    wire [63:0] adapter_rx_payload;
    wire elastic_data_valid;
    wire elastic_data_ready;
    wire [15:0] elastic_data_sequence;
    wire [63:0] elastic_data_payload;
    wire arbitration_error;
    wire endpoint_error;
    wire adapter_error;
    wire tx_fifo_overflow;
    wire rx_fifo_overflow;
    wire dejitter_overflow;
    wire dejitter_underflow;
    wire dejitter_sequence_error;
    wire control_reset = fabric_reset || !link_ready;

    emuflow_runtime_sync_control_endpoint #(
        .ROLE_PARENT(ROLE_PARENT)
    ) control_endpoint (
        .fabric_clk(fabric_clk), .reset(control_reset),
        .local_subtree_ready(local_subtree_ready),
        .local_start_valid(local_start_valid),
        .local_start_epoch(local_start_epoch),
        .remote_subtree_ready(remote_subtree_ready),
        .remote_start_valid(remote_start_valid),
        .remote_start_epoch(remote_start_epoch),
        .tx_control_valid(control_tx_valid),
        .tx_control_ready(control_tx_ready),
        .tx_control_kind(control_tx_kind),
        .tx_control_sequence(control_tx_sequence),
        .tx_control_payload(control_tx_payload),
        .rx_control_valid(control_rx_valid),
        .rx_control_ready(control_rx_ready),
        .rx_control_kind(control_rx_kind),
        .rx_control_sequence(control_rx_sequence),
        .rx_control_payload(control_rx_payload),
        .protocol_error(endpoint_error)
    );

    emuflow_record_mux record_mux (
        .clk(fabric_clk), .reset(fabric_reset),
        .data_valid(data_tx_valid), .data_ready(data_tx_ready),
        .data_sequence(data_tx_sequence), .data_payload(data_tx_payload),
        .control_valid(control_tx_valid),
        .control_ready(control_tx_ready),
        .control_kind(control_tx_kind),
        .control_sequence(control_tx_sequence),
        .control_payload(control_tx_payload),
        .output_valid(mux_valid), .output_ready(mux_ready),
        .output_kind(mux_kind), .output_sequence(mux_sequence),
        .output_payload(mux_payload),
        .arbitration_error(arbitration_error)
    );

    emuflow_10g_pcs_cdc_adapter #(
        .FIFO_DEPTH(FIFO_DEPTH), .BIT_REVERSE(BIT_REVERSE),
        .COUNT_125US(COUNT_125US)
    ) cdc_adapter (
        .fabric_clk(fabric_clk), .fabric_reset(fabric_reset),
        .tx_record_valid(mux_valid), .tx_record_ready(mux_ready),
        .tx_record_kind(mux_kind), .tx_record_sequence(mux_sequence),
        .tx_record_payload(mux_payload),
        .tx_fifo_overflow(tx_fifo_overflow),
        .rx_record_valid(adapter_rx_valid),
        .rx_record_ready(adapter_rx_ready),
        .rx_record_kind(adapter_rx_kind),
        .rx_record_sequence(adapter_rx_sequence),
        .rx_record_payload(adapter_rx_payload),
        .rx_fifo_overflow(rx_fifo_overflow),
        .pcs_tx_clk(pcs_tx_clk), .pcs_tx_reset(pcs_tx_reset),
        .pcs_rx_clk(pcs_rx_clk), .pcs_rx_reset(pcs_rx_reset),
        .serdes_tx_data(serdes_tx_data), .serdes_tx_hdr(serdes_tx_hdr),
        .serdes_rx_data(serdes_rx_data), .serdes_rx_hdr(serdes_rx_hdr),
        .serdes_rx_bitslip(serdes_rx_bitslip),
        .serdes_rx_reset_req(serdes_rx_reset_req),
        .link_ready(link_ready), .link_error(adapter_error)
    );

    emuflow_record_demux record_demux (
        .input_valid(adapter_rx_valid), .input_ready(adapter_rx_ready),
        .input_kind(adapter_rx_kind),
        .input_sequence(adapter_rx_sequence),
        .input_payload(adapter_rx_payload),
        .data_valid(elastic_data_valid), .data_ready(elastic_data_ready),
        .data_sequence(elastic_data_sequence),
        .data_payload(elastic_data_payload),
        .control_valid(control_rx_valid), .control_ready(control_rx_ready),
        .control_kind(control_rx_kind),
        .control_sequence(control_rx_sequence),
        .control_payload(control_rx_payload)
    );

    emuflow_record_dejitter_buffer #(
        .DEPTH(FIFO_DEPTH), .START_THRESHOLD(DEJITTER_THRESHOLD)
    ) dejitter (
        .fabric_clk(fabric_clk), .reset(fabric_reset),
        .input_valid(elastic_data_valid), .input_ready(elastic_data_ready),
        .input_sequence(elastic_data_sequence),
        .input_payload(elastic_data_payload),
        .output_valid(data_rx_valid), .output_ready(data_rx_ready),
        .output_sequence(data_rx_sequence),
        .output_payload(data_rx_payload),
        .release_started(release_started),
        .overflow_error(dejitter_overflow),
        .underflow_error(dejitter_underflow),
        .sequence_error(dejitter_sequence_error)
    );

    assign edge_error =
        arbitration_error || endpoint_error || adapter_error ||
        tx_fifo_overflow || rx_fifo_overflow || dejitter_overflow ||
        dejitter_underflow || dejitter_sequence_error;
endmodule
