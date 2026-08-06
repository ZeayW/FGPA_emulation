// Copyright 2026 EmuFlow contributors
// SPDX-License-Identifier: Apache-2.0
module emuflow_record_mux (
    input  wire        clk,
    input  wire        reset,
    input  wire        data_valid,
    output wire        data_ready,
    input  wire [15:0] data_sequence,
    input  wire [63:0] data_payload,
    input  wire        control_valid,
    output wire        control_ready,
    input  wire [1:0]  control_kind,
    input  wire [15:0] control_sequence,
    input  wire [63:0] control_payload,
    output wire        output_valid,
    input  wire        output_ready,
    output wire [1:0]  output_kind,
    output wire [15:0] output_sequence,
    output wire [63:0] output_payload,
    output reg         arbitration_error
);
    // Control is legal only while the runtime data stream is stopped.  Give
    // it priority for safe startup, but make any overlap a visible fault.
    assign output_valid = control_valid || data_valid;
    assign output_kind = control_valid ? control_kind : 2'b00;
    assign output_sequence =
        control_valid ? control_sequence : data_sequence;
    assign output_payload = control_valid ? control_payload : data_payload;
    assign control_ready = output_ready;
    assign data_ready = output_ready && !control_valid;

    always @(posedge clk) begin
        if (reset)
            arbitration_error <= 1'b0;
        else if (control_valid && data_valid)
            arbitration_error <= 1'b1;
    end
endmodule
