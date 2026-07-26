module emuflow_frame_barrier #(
    parameter integer FRAME_SLOTS = 32
) (
    input  wire clk,
    input  wire reset,
    input  wire links_ready,
    output reg  virtual_clock_enable,
    output reg  [31:0] slot
);
    always @(posedge clk) begin
        if (reset) begin
            slot <= 0;
            virtual_clock_enable <= 1'b0;
        end else begin
            virtual_clock_enable <= 1'b0;
            if (slot == FRAME_SLOTS - 1) begin
                if (links_ready) begin
                    slot <= 0;
                    virtual_clock_enable <= 1'b1;
                end
            end else begin
                slot <= slot + 1;
            end
        end
    end
endmodule
