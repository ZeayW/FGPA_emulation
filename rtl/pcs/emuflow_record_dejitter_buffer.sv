// Copyright 2026 EmuFlow contributors
// SPDX-License-Identifier: Apache-2.0
//
// Converts an elastic CDC record stream into one deterministic record per
// phase-aligned fabric cycle after a fixed prefill threshold.
module emuflow_record_dejitter_buffer #(
    parameter integer DEPTH = 32,
    parameter integer START_THRESHOLD = 8
) (
    input  wire        fabric_clk,
    input  wire        reset,
    input  wire        input_valid,
    output wire        input_ready,
    input  wire [15:0] input_sequence,
    input  wire [63:0] input_payload,
    output wire        output_valid,
    input  wire        output_ready,
    output wire [15:0] output_sequence,
    output wire [63:0] output_payload,
    output wire        release_started,
    output reg         overflow_error,
    output reg         underflow_error,
    output reg         sequence_error
);
    localparam integer ADDRESS_BITS = $clog2(DEPTH);
    reg [15:0] sequence_memory [0:DEPTH-1];
    reg [63:0] payload_memory [0:DEPTH-1];
    reg [ADDRESS_BITS-1:0] write_pointer;
    reg [ADDRESS_BITS-1:0] read_pointer;
    reg [ADDRESS_BITS:0] count;
    reg started;
    reg expected_valid;
    reg [15:0] expected_sequence;

    wire write_enable = input_valid && input_ready;
    wire read_enable = output_valid && output_ready;

    initial begin
        if (DEPTH < 4 || (DEPTH & (DEPTH - 1)) != 0)
            $error("de-jitter DEPTH must be a power of two >= 4");
        if (START_THRESHOLD < 2 || START_THRESHOLD >= DEPTH)
            $error("de-jitter START_THRESHOLD must satisfy 2 <= value < DEPTH");
    end

    assign input_ready = count < DEPTH;
    assign output_valid = started && count != 0;
    assign output_sequence = sequence_memory[read_pointer];
    assign output_payload = payload_memory[read_pointer];
    assign release_started = started;

    always @(posedge fabric_clk) begin
        if (reset) begin
            write_pointer <= 0;
            read_pointer <= 0;
            count <= 0;
            started <= 1'b0;
            expected_valid <= 1'b0;
            expected_sequence <= 0;
            overflow_error <= 1'b0;
            underflow_error <= 1'b0;
            sequence_error <= 1'b0;
        end else begin
            if (input_valid && !input_ready)
                overflow_error <= 1'b1;
            if (write_enable) begin
                sequence_memory[write_pointer] <= input_sequence;
                payload_memory[write_pointer] <= input_payload;
                write_pointer <= write_pointer + 1'b1;
                if (!expected_valid) begin
                    expected_sequence <= input_sequence;
                    expected_valid <= 1'b1;
                end
            end

            if (!started &&
                count + (write_enable ? 1 : 0) >= START_THRESHOLD)
                started <= 1'b1;

            if (started && count == 0)
                underflow_error <= 1'b1;

            if (read_enable) begin
                if (!expected_valid || output_sequence != expected_sequence)
                    sequence_error <= 1'b1;
                expected_sequence <= expected_sequence + 1'b1;
                read_pointer <= read_pointer + 1'b1;
            end

            case ({write_enable, read_enable})
                2'b10: count <= count + 1'b1;
                2'b01: count <= count - 1'b1;
                default: count <= count;
            endcase
        end
    end
endmodule
