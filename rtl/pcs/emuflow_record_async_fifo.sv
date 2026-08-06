// Copyright 2026 EmuFlow contributors
// SPDX-License-Identifier: Apache-2.0
//
// Typed record wrapper around Corundum's MIT-licensed Gray-pointer AXI async
// FIFO.  The upstream FIFO source is retained unchanged under engines/.
module emuflow_record_async_fifo #(
    parameter integer DEPTH = 32
) (
    input  wire        source_clk,
    input  wire        source_reset,
    input  wire        source_valid,
    output wire        source_ready,
    input  wire [1:0]  source_kind,
    input  wire [15:0] source_sequence,
    input  wire [63:0] source_payload,
    output wire        source_overflow,

    input  wire        sink_clk,
    input  wire        sink_reset,
    output wire        sink_valid,
    input  wire        sink_ready,
    output wire [1:0]  sink_kind,
    output wire [15:0] sink_sequence,
    output wire [63:0] sink_payload
);
    localparam integer RECORD_WIDTH = 82;
    wire [RECORD_WIDTH-1:0] source_record = {
        source_kind, source_sequence, source_payload
    };
    wire [RECORD_WIDTH-1:0] sink_record;

    initial begin
        if (DEPTH < 4 || (DEPTH & (DEPTH - 1)) != 0)
            $error("record async FIFO DEPTH must be a power of two >= 4");
    end

    axis_async_fifo #(
        .DEPTH(DEPTH),
        .DATA_WIDTH(RECORD_WIDTH),
        .KEEP_ENABLE(0), .KEEP_WIDTH(1),
        .LAST_ENABLE(0),
        .ID_ENABLE(0), .ID_WIDTH(1),
        .DEST_ENABLE(0), .DEST_WIDTH(1),
        .USER_ENABLE(0), .USER_WIDTH(1),
        .RAM_PIPELINE(1), .OUTPUT_FIFO_ENABLE(1),
        .FRAME_FIFO(0), .PAUSE_ENABLE(0)
    ) fifo (
        .s_clk(source_clk), .s_rst(source_reset),
        .s_axis_tdata(source_record), .s_axis_tkeep(1'b1),
        .s_axis_tvalid(source_valid), .s_axis_tready(source_ready),
        .s_axis_tlast(1'b0), .s_axis_tid(1'b0),
        .s_axis_tdest(1'b0), .s_axis_tuser(1'b0),
        .m_clk(sink_clk), .m_rst(sink_reset),
        .m_axis_tdata(sink_record), .m_axis_tvalid(sink_valid),
        .m_axis_tready(sink_ready),
        .s_pause_req(1'b0), .m_pause_req(1'b0),
        .s_status_overflow(source_overflow)
    );

    assign sink_kind = sink_record[81:80];
    assign sink_sequence = sink_record[79:64];
    assign sink_payload = sink_record[63:0];
endmodule
