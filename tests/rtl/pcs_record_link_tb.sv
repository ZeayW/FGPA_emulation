`timescale 1ns/1ps
module pcs_record_link_tb;
    reg clk = 1'b0;
    reg reset = 1'b1;
    always #3.2 clk = ~clk;

    reg tx_valid = 1'b0;
    wire tx_ready;
    reg [1:0] tx_kind = 2'b0;
    reg [15:0] tx_sequence = 16'b0;
    reg [63:0] tx_payload = 64'b0;
    wire rx_valid;
    reg rx_ready = 1'b1;
    wire [1:0] rx_kind;
    wire [15:0] rx_sequence;
    wire [63:0] rx_payload;
    wire [63:0] serdes_data;
    wire [1:0] serdes_hdr;
    wire bitslip;
    wire reset_req;
    wire tx_bad_block;
    wire [6:0] rx_error_count;
    wire rx_bad_block;
    wire rx_sequence_error;
    wire rx_block_lock;
    wire rx_high_ber;
    wire rx_status;
    wire framing_error;
    wire crc_error;
    wire overflow_error;

    emuflow_10g_pcs_record_link #(
        .COUNT_125US(64)
    ) dut (
        .tx_clk(clk), .tx_reset(reset),
        .tx_record_valid(tx_valid), .tx_record_ready(tx_ready),
        .tx_record_kind(tx_kind), .tx_record_sequence(tx_sequence),
        .tx_record_payload(tx_payload),
        .rx_clk(clk), .rx_reset(reset),
        .rx_record_valid(rx_valid), .rx_record_ready(rx_ready),
        .rx_record_kind(rx_kind), .rx_record_sequence(rx_sequence),
        .rx_record_payload(rx_payload),
        .serdes_tx_data(serdes_data), .serdes_tx_hdr(serdes_hdr),
        .serdes_rx_data(serdes_data), .serdes_rx_hdr(serdes_hdr),
        .serdes_rx_bitslip(bitslip), .serdes_rx_reset_req(reset_req),
        .tx_bad_block(tx_bad_block), .rx_error_count(rx_error_count),
        .rx_bad_block(rx_bad_block),
        .rx_sequence_error(rx_sequence_error),
        .rx_block_lock(rx_block_lock), .rx_high_ber(rx_high_ber),
        .rx_status(rx_status),
        .record_framing_error(framing_error),
        .record_crc_error(crc_error),
        .record_overflow_error(overflow_error)
    );

    integer sent = 0;
    integer received = 0;
    integer timeout = 0;
    reg [1:0] expected_kind [0:63];
    reg [15:0] expected_sequence [0:63];
    reg [63:0] expected_payload [0:63];

    always @(posedge clk) begin
        if (!reset && tx_valid && tx_ready)
            sent <= sent + 1;
        if (!reset && rx_valid && rx_ready) begin
            if (received >= sent)
                $fatal(1, "received a record that was never accepted");
            if (rx_kind !== expected_kind[received] ||
                rx_sequence !== expected_sequence[received] ||
                rx_payload !== expected_payload[received])
                $fatal(1, "record mismatch at index %0d", received);
            received <= received + 1;
        end
    end

    initial begin
        repeat (8) @(posedge clk);
        @(negedge clk); reset = 1'b0;
        while (!rx_block_lock && timeout < 10000) begin
            @(posedge clk); timeout = timeout + 1;
        end
        if (!rx_block_lock)
            $fatal(1, "PCS did not acquire block lock");

        while (sent < 64) begin
            @(negedge clk);
            tx_valid = 1'b1;
            tx_kind = sent[1:0];
            tx_sequence = 16'h4000 + sent;
            tx_payload = 64'h0123456789abcdef ^ sent;
            expected_kind[sent] = sent[1:0];
            expected_sequence[sent] = 16'h4000 + sent;
            expected_payload[sent] = 64'h0123456789abcdef ^ sent;
            @(posedge clk);
            if (!tx_ready)
                @(negedge clk);
        end
        @(negedge clk); tx_valid = 1'b0;

        timeout = 0;
        while (received < 64 && timeout < 10000) begin
            @(posedge clk); timeout = timeout + 1;
        end
        if (received != 64)
            $fatal(1, "record receive timeout: %0d", received);
        if (framing_error || crc_error || overflow_error || tx_bad_block ||
            rx_bad_block || rx_sequence_error || rx_high_ber)
            $fatal(1, "PCS or record layer reported an error");
        $display("EMUFLOW_PCS_RECORD_TB status=pass records=%0d", received);
        $finish;
    end
endmodule

module pcs_record_crc_tb;
    reg clk = 1'b0;
    reg reset = 1'b1;
    always #3.2 clk = ~clk;
    reg tx_valid = 1'b0;
    wire tx_ready;
    wire [63:0] xgmii_txd;
    wire [7:0] xgmii_txc;
    wire [63:0] corrupted_txd =
        xgmii_txc == 8'h00 ? xgmii_txd ^ 64'h1 : xgmii_txd;
    wire rx_valid;
    wire [1:0] rx_kind;
    wire [15:0] rx_sequence;
    wire [63:0] rx_payload;
    wire framing_error;
    wire crc_error;
    wire overflow_error;

    emuflow_xgmii_record_framer framer (
        .clk(clk), .reset(reset),
        .record_valid(tx_valid), .record_ready(tx_ready),
        .record_kind(2'b00), .record_sequence(16'h1234),
        .record_payload(64'hfedcba9876543210),
        .xgmii_txd(xgmii_txd), .xgmii_txc(xgmii_txc)
    );
    emuflow_xgmii_record_deframer deframer (
        .clk(clk), .reset(reset),
        .xgmii_rxd(corrupted_txd), .xgmii_rxc(xgmii_txc),
        .record_valid(rx_valid), .record_ready(1'b1),
        .record_kind(rx_kind), .record_sequence(rx_sequence),
        .record_payload(rx_payload),
        .framing_error(framing_error), .crc_error(crc_error),
        .overflow_error(overflow_error)
    );

    initial begin
        repeat (4) @(posedge clk);
        @(negedge clk); reset = 1'b0; tx_valid = 1'b1;
        while (!tx_ready) @(posedge clk);
        @(negedge clk); tx_valid = 1'b0;
        repeat (8) @(posedge clk);
        if (!crc_error || rx_valid || framing_error || overflow_error)
            $fatal(1, "corrupted record was not rejected by CRC");
        $display("EMUFLOW_PCS_CRC_TB status=pass");
        $finish;
    end
endmodule

module pcs_record_cdc_tb;
    reg fabric_clk = 1'b0;
    reg pcs_clk = 1'b0;
    reg fabric_reset = 1'b1;
    reg pcs_reset = 1'b1;
    always #10 fabric_clk = ~fabric_clk;
    always #3.2 pcs_clk = ~pcs_clk;

    reg tx_valid = 1'b0;
    wire tx_ready;
    reg [1:0] tx_kind = 2'b0;
    reg [15:0] tx_sequence = 16'b0;
    reg [63:0] tx_payload = 64'b0;
    wire rx_valid;
    wire [1:0] rx_kind;
    wire [15:0] rx_sequence;
    wire [63:0] rx_payload;
    wire [63:0] serdes_data;
    wire [1:0] serdes_hdr;
    wire bitslip;
    wire reset_req;
    wire link_ready;
    wire link_error;
    wire tx_fifo_overflow;
    wire rx_fifo_overflow;

    emuflow_10g_pcs_cdc_adapter #(
        .FIFO_DEPTH(32), .COUNT_125US(65)
    ) dut (
        .fabric_clk(fabric_clk), .fabric_reset(fabric_reset),
        .tx_record_valid(tx_valid), .tx_record_ready(tx_ready),
        .tx_record_kind(tx_kind), .tx_record_sequence(tx_sequence),
        .tx_record_payload(tx_payload),
        .tx_fifo_overflow(tx_fifo_overflow),
        .rx_record_valid(rx_valid), .rx_record_ready(1'b1),
        .rx_record_kind(rx_kind), .rx_record_sequence(rx_sequence),
        .rx_record_payload(rx_payload),
        .rx_fifo_overflow(rx_fifo_overflow),
        .pcs_tx_clk(pcs_clk), .pcs_tx_reset(pcs_reset),
        .pcs_rx_clk(pcs_clk), .pcs_rx_reset(pcs_reset),
        .serdes_tx_data(serdes_data), .serdes_tx_hdr(serdes_hdr),
        .serdes_rx_data(serdes_data), .serdes_rx_hdr(serdes_hdr),
        .serdes_rx_bitslip(bitslip), .serdes_rx_reset_req(reset_req),
        .link_ready(link_ready), .link_error(link_error)
    );

    integer sent = 0;
    integer received = 0;
    integer timeout = 0;
    always @(posedge fabric_clk) begin
        if (!fabric_reset && tx_valid && tx_ready)
            sent <= sent + 1;
        if (!fabric_reset && rx_valid) begin
            if (rx_kind !== received[1:0] ||
                rx_sequence !== 16'h8000 + received ||
                rx_payload !== (64'h55aa000000000000 | received))
                $fatal(1, "CDC record mismatch at index %0d", received);
            received <= received + 1;
        end
    end

    initial begin
        repeat (8) @(posedge pcs_clk);
        @(negedge pcs_clk); pcs_reset = 1'b0;
        repeat (5) @(posedge fabric_clk);
        @(negedge fabric_clk); fabric_reset = 1'b0;
        while (!link_ready && timeout < 10000) begin
            @(posedge pcs_clk); timeout = timeout + 1;
        end
        if (!link_ready)
            $fatal(1, "CDC PCS not ready: lock=%0d status=%0d error=%0d bitslip=%0d reset_req=%0d",
                dut.rx_block_lock, dut.rx_status, link_error,
                bitslip, reset_req);

        while (sent < 256) begin
            @(negedge fabric_clk);
            tx_valid = 1'b1;
            tx_kind = sent[1:0];
            tx_sequence = 16'h8000 + sent;
            tx_payload = 64'h55aa000000000000 | sent;
        end
        tx_valid = 1'b0;

        timeout = 0;
        while (received < 256 && timeout < 10000) begin
            @(posedge fabric_clk); timeout = timeout + 1;
        end
        if (received != 256)
            $fatal(1, "CDC receive timeout: %0d", received);
        if (link_error || tx_fifo_overflow || rx_fifo_overflow)
            $fatal(1, "CDC adapter reported an error");
        $display("EMUFLOW_PCS_CDC_TB status=pass records=%0d", received);
        $finish;
    end
endmodule

module pcs_record_dejitter_tb;
    reg clk = 1'b0;
    reg reset = 1'b1;
    always #10 clk = ~clk;
    reg input_valid = 1'b0;
    wire input_ready;
    reg [15:0] input_sequence = 16'b0;
    reg [63:0] input_payload = 64'b0;
    wire output_valid;
    wire [15:0] output_sequence;
    wire [63:0] output_payload;
    wire release_started;
    wire overflow_error;
    wire underflow_error;
    wire sequence_error;

    emuflow_record_dejitter_buffer #(
        .DEPTH(32), .START_THRESHOLD(8)
    ) dut (
        .fabric_clk(clk), .reset(reset),
        .input_valid(input_valid), .input_ready(input_ready),
        .input_sequence(input_sequence), .input_payload(input_payload),
        .output_valid(output_valid), .output_ready(1'b1),
        .output_sequence(output_sequence), .output_payload(output_payload),
        .release_started(release_started),
        .overflow_error(overflow_error),
        .underflow_error(underflow_error),
        .sequence_error(sequence_error)
    );

    integer sent = 0;
    integer received = 0;
    always @(posedge clk) begin
        if (!reset && input_valid && input_ready)
            sent <= sent + 1;
        if (!reset && release_started && received < 64 && !output_valid)
            $fatal(1, "de-jitter output developed a gap after release");
        if (!reset && output_valid) begin
            if (output_sequence !== 16'h2000 + received ||
                output_payload !== (64'hface000000000000 | received))
                $fatal(1, "de-jitter record mismatch at index %0d", received);
            received <= received + 1;
        end
    end

    initial begin
        repeat (4) @(posedge clk);
        @(negedge clk); reset = 1'b0;
        while (sent < 64) begin
            @(negedge clk);
            input_valid = 1'b1;
            input_sequence = 16'h2000 + sent;
            input_payload = 64'hface000000000000 | sent;
        end
        input_valid = 1'b0;
        wait (received == 64);
        #1;
        if (overflow_error || underflow_error || sequence_error)
            $fatal(1, "de-jitter error: overflow=%0d underflow=%0d sequence=%0d",
                overflow_error, underflow_error, sequence_error);
        $display("EMUFLOW_PCS_DEJITTER_TB status=pass records=%0d", received);
        $finish;
    end
endmodule
