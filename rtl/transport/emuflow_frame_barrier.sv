module emuflow_frame_barrier #(
    parameter integer FRAME_SLOTS = 32
) (
    input  wire clk,
    input  wire reset,
    input  wire links_ready,
    output wire virtual_clock_enable,
    output reg  [31:0] slot
);
    assign virtual_clock_enable =
        !reset && links_ready && (slot == FRAME_SLOTS - 1);

    always @(posedge clk) begin
        if (reset) begin
            slot <= 0;
        end else begin
            if (slot == FRAME_SLOTS - 1) begin
                if (links_ready) begin
                    slot <= 0;
                end
            end else begin
                slot <= slot + 1;
            end
        end
    end
endmodule
