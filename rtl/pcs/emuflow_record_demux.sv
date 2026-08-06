// Copyright 2026 EmuFlow contributors
// SPDX-License-Identifier: Apache-2.0
module emuflow_record_demux (
    input  wire        input_valid,
    output wire        input_ready,
    input  wire [1:0]  input_kind,
    input  wire [15:0] input_sequence,
    input  wire [63:0] input_payload,
    output wire        data_valid,
    input  wire        data_ready,
    output wire [15:0] data_sequence,
    output wire [63:0] data_payload,
    output wire        control_valid,
    input  wire        control_ready,
    output wire [1:0]  control_kind,
    output wire [15:0] control_sequence,
    output wire [63:0] control_payload
);
    wire is_data = input_kind == 2'b00;
    assign data_valid = input_valid && is_data;
    assign control_valid = input_valid && !is_data;
    assign input_ready = is_data ? data_ready : control_ready;
    assign data_sequence = input_sequence;
    assign data_payload = input_payload;
    assign control_kind = input_kind;
    assign control_sequence = input_sequence;
    assign control_payload = input_payload;
endmodule
