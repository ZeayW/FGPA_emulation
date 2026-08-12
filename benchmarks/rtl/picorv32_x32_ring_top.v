// Scalable connected open-RTL stress harness for the pinned PicoRV32 core.
//
// The registered ring and eight-neighbour IRQ coupling make every core
// communicate with a local window of peers.  This prevents a multi-FPGA
// partition from placing whole, independent cores while producing only one or
// two boundary signals, which would make Phase 6 pin planning nearly vacuous.
module picorv32_x32_ring_top (
    input wire clk,
    input wire resetn,

    input wire [31:0] mem_ready,
    input wire [1023:0] mem_rdata,
    input wire [31:0] pcpi_wr,
    input wire [1023:0] pcpi_rd,
    input wire [31:0] pcpi_wait,
    input wire [31:0] pcpi_ready,
    input wire [1023:0] irq,

    output wire [31:0] trap,
    output wire [31:0] mem_valid,
    output wire [31:0] mem_instr,
    output wire [1023:0] mem_addr,
    output wire [1023:0] mem_wdata,
    output wire [127:0] mem_wstrb,
    output wire [31:0] mem_la_read,
    output wire [31:0] mem_la_write,
    output wire [1023:0] mem_la_addr,
    output wire [1023:0] mem_la_wdata,
    output wire [127:0] mem_la_wstrb,
    output wire [31:0] pcpi_valid,
    output wire [1023:0] pcpi_insn,
    output wire [1023:0] pcpi_rs1,
    output wire [1023:0] pcpi_rs2,
    output wire [1023:0] eoi,
    output wire [31:0] trace_valid,
    output wire [1151:0] trace_data,
    output wire [31:0] ring_state_out
);
    reg [31:0] ring_state;
    wire [31:0] activity = mem_valid ^ mem_instr ^ trap;

    always @(posedge clk) begin
        if (!resetn)
            ring_state <= 32'b0;
        else
            ring_state <= {ring_state[30:0], ring_state[31]} ^ activity;
    end

    assign ring_state_out = ring_state;

    genvar core;
    generate
        for (core = 0; core < 32; core = core + 1) begin : cores
            wire [7:0] ring_irq = {
                ring_state[(core + 7) % 32],
                ring_state[(core + 6) % 32],
                ring_state[(core + 5) % 32],
                ring_state[(core + 4) % 32],
                ring_state[(core + 3) % 32],
                ring_state[(core + 2) % 32],
                ring_state[(core + 1) % 32],
                ring_state[core % 32]
            };
            wire [31:0] coupled_irq = irq[core*32 +: 32];
            wire coupled_mem_ready =
                mem_ready[core] ^ ^ring_irq;

            picorv32 cpu (
                .clk(clk),
                .resetn(resetn),
                .trap(trap[core]),
                .mem_valid(mem_valid[core]),
                .mem_instr(mem_instr[core]),
                .mem_ready(coupled_mem_ready),
                .mem_addr(mem_addr[core*32 +: 32]),
                .mem_wdata(mem_wdata[core*32 +: 32]),
                .mem_wstrb(mem_wstrb[core*4 +: 4]),
                .mem_rdata(mem_rdata[core*32 +: 32]),
                .mem_la_read(mem_la_read[core]),
                .mem_la_write(mem_la_write[core]),
                .mem_la_addr(mem_la_addr[core*32 +: 32]),
                .mem_la_wdata(mem_la_wdata[core*32 +: 32]),
                .mem_la_wstrb(mem_la_wstrb[core*4 +: 4]),
                .pcpi_valid(pcpi_valid[core]),
                .pcpi_insn(pcpi_insn[core*32 +: 32]),
                .pcpi_rs1(pcpi_rs1[core*32 +: 32]),
                .pcpi_rs2(pcpi_rs2[core*32 +: 32]),
                .pcpi_wr(pcpi_wr[core]),
                .pcpi_rd(pcpi_rd[core*32 +: 32]),
                .pcpi_wait(pcpi_wait[core]),
                .pcpi_ready(pcpi_ready[core]),
                .irq(coupled_irq),
                .eoi(eoi[core*32 +: 32]),
                .trace_valid(trace_valid[core]),
                .trace_data(trace_data[core*36 +: 36])
            );
        end
    endgenerate
endmodule
