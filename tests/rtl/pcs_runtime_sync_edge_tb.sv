`timescale 1ns/1ps
module pcs_runtime_sync_edge_tb;
    reg fabric_clk = 1'b0;
    reg pcs_clk = 1'b0;
    reg fabric_reset = 1'b1;
    reg pcs_reset = 1'b1;
    always #10 fabric_clk = ~fabric_clk;
    always #3.2 pcs_clk = ~pcs_clk;

    reg child_subtree_ready = 1'b0;
    reg parent_start_valid = 1'b0;
    reg [31:0] parent_start_epoch = 32'b0;
    wire parent_remote_ready;
    wire child_remote_start_valid;
    wire [31:0] child_remote_start_epoch;

    reg parent_data_valid = 1'b0;
    wire parent_data_ready;
    reg [15:0] parent_data_sequence = 16'b0;
    reg [63:0] parent_data_payload = 64'b0;
    wire child_data_valid;
    wire [15:0] child_data_sequence;
    wire [63:0] child_data_payload;

    wire [63:0] parent_tx_data;
    wire [1:0] parent_tx_hdr;
    wire [63:0] child_tx_data;
    wire [1:0] child_tx_hdr;
    wire parent_link_ready;
    wire child_link_ready;
    wire parent_error;
    wire child_error;

    emuflow_runtime_sync_pcs_edge #(
        .ROLE_PARENT(1), .FIFO_DEPTH(32),
        .DEJITTER_THRESHOLD(8), .COUNT_125US(65)
    ) parent_edge (
        .fabric_clk(fabric_clk), .fabric_reset(fabric_reset),
        .data_tx_valid(parent_data_valid),
        .data_tx_ready(parent_data_ready),
        .data_tx_sequence(parent_data_sequence),
        .data_tx_payload(parent_data_payload),
        .data_rx_valid(), .data_rx_ready(1'b1),
        .data_rx_sequence(), .data_rx_payload(),
        .local_subtree_ready(1'b0),
        .local_start_valid(parent_start_valid),
        .local_start_epoch(parent_start_epoch),
        .remote_subtree_ready(parent_remote_ready),
        .remote_start_valid(), .remote_start_epoch(),
        .pcs_tx_clk(pcs_clk), .pcs_tx_reset(pcs_reset),
        .pcs_rx_clk(pcs_clk), .pcs_rx_reset(pcs_reset),
        .serdes_tx_data(parent_tx_data), .serdes_tx_hdr(parent_tx_hdr),
        .serdes_rx_data(child_tx_data), .serdes_rx_hdr(child_tx_hdr),
        .serdes_rx_bitslip(), .serdes_rx_reset_req(),
        .link_ready(parent_link_ready), .release_started(),
        .edge_error(parent_error)
    );

    emuflow_runtime_sync_pcs_edge #(
        .ROLE_PARENT(0), .FIFO_DEPTH(32),
        .DEJITTER_THRESHOLD(8), .COUNT_125US(65)
    ) child_edge (
        .fabric_clk(fabric_clk), .fabric_reset(fabric_reset),
        .data_tx_valid(1'b0), .data_tx_ready(),
        .data_tx_sequence(16'b0), .data_tx_payload(64'b0),
        .data_rx_valid(child_data_valid), .data_rx_ready(1'b1),
        .data_rx_sequence(child_data_sequence),
        .data_rx_payload(child_data_payload),
        .local_subtree_ready(child_subtree_ready),
        .local_start_valid(1'b0), .local_start_epoch(32'b0),
        .remote_subtree_ready(),
        .remote_start_valid(child_remote_start_valid),
        .remote_start_epoch(child_remote_start_epoch),
        .pcs_tx_clk(pcs_clk), .pcs_tx_reset(pcs_reset),
        .pcs_rx_clk(pcs_clk), .pcs_rx_reset(pcs_reset),
        .serdes_tx_data(child_tx_data), .serdes_tx_hdr(child_tx_hdr),
        .serdes_rx_data(parent_tx_data), .serdes_rx_hdr(parent_tx_hdr),
        .serdes_rx_bitslip(), .serdes_rx_reset_req(),
        .link_ready(child_link_ready), .release_started(),
        .edge_error(child_error)
    );

    integer sent = 0;
    integer received = 0;
    integer timeout = 0;
    always @(posedge fabric_clk) begin
        if (!fabric_reset && parent_data_valid && parent_data_ready)
            sent <= sent + 1;
        if (!fabric_reset && child_data_valid) begin
            if (child_data_sequence !== received ||
                child_data_payload !== (64'hcafe000000000000 | received))
                $fatal(1, "edge data mismatch at index %0d", received);
            received <= received + 1;
        end
    end

    initial begin
        repeat (8) @(posedge pcs_clk);
        @(negedge pcs_clk); pcs_reset = 1'b0;
        repeat (5) @(posedge fabric_clk);
        @(negedge fabric_clk); fabric_reset = 1'b0;
        while (!(parent_link_ready && child_link_ready) && timeout < 10000) begin
            @(posedge pcs_clk); timeout = timeout + 1;
        end
        if (!(parent_link_ready && child_link_ready))
            $fatal(1, "full-duplex PCS edge did not become ready");

        @(negedge fabric_clk); child_subtree_ready = 1'b1;
        wait (parent_remote_ready == 1'b1);
        @(negedge fabric_clk);
        parent_start_epoch = 32'h34567890;
        parent_start_valid = 1'b1;
        wait (child_remote_start_valid == 1'b1);
        if (child_remote_start_epoch !== 32'h34567890)
            $fatal(1, "in-band START epoch mismatch");
        repeat (4) @(posedge fabric_clk);

        while (sent < 64) begin
            @(negedge fabric_clk);
            parent_data_valid = 1'b1;
            parent_data_sequence = sent;
            parent_data_payload = 64'hcafe000000000000 | sent;
        end
        parent_data_valid = 1'b0;
        wait (received == 64);
        #1;
        if (parent_error || child_error)
            $fatal(1, "full PCS edge reported an error");
        $display("EMUFLOW_PCS_RUNTIME_SYNC_EDGE_TB status=pass records=%0d", received);
        $finish;
    end
endmodule
