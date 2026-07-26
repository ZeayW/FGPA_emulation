// Scalable open-RTL stress harness for the pinned PicoRV32 core.
//
// Each core has independent data/handshake/IRQ inputs and exported outputs so
// synthesis cannot merge identical cores or prune their observable logic.
module picorv32_x32_top (
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
    output wire [1151:0] trace_data
);
    genvar core;
    generate
        for (core = 0; core < 32; core = core + 1) begin : cores
            picorv32 cpu (
                .clk(clk),
                .resetn(resetn),
                .trap(trap[core]),
                .mem_valid(mem_valid[core]),
                .mem_instr(mem_instr[core]),
                .mem_ready(mem_ready[core]),
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
                .irq(irq[core*32 +: 32]),
                .eoi(eoi[core*32 +: 32]),
                .trace_valid(trace_valid[core]),
                .trace_data(trace_data[core*36 +: 36])
            );
        end
    endgenerate
endmodule
