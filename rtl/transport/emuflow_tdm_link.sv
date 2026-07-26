module emuflow_tdm_link #(
    parameter integer LANES = 1,
    parameter integer LATENCY = 1
) (
    input  wire                 clk,
    input  wire                 reset,
    input  wire [LANES-1:0]     tx_data,
    input  wire [LANES-1:0]     tx_valid,
    output wire [LANES-1:0]     rx_data,
    output wire [LANES-1:0]     rx_valid
);
    reg [LANES-1:0] data_pipe [0:LATENCY];
    reg [LANES-1:0] valid_pipe [0:LATENCY];
    integer stage;

    always @(posedge clk) begin
        if (reset) begin
            for (stage = 0; stage <= LATENCY; stage = stage + 1) begin
                data_pipe[stage] <= {LANES{1'b0}};
                valid_pipe[stage] <= {LANES{1'b0}};
            end
        end else begin
            data_pipe[0] <= tx_data;
            valid_pipe[0] <= tx_valid;
            for (stage = 1; stage <= LATENCY; stage = stage + 1) begin
                data_pipe[stage] <= data_pipe[stage-1];
                valid_pipe[stage] <= valid_pipe[stage-1];
            end
        end
    end

    assign rx_data = data_pipe[LATENCY];
    assign rx_valid = valid_pipe[LATENCY];
endmodule
