// Copyright 2026 EmuFlow contributors
// SPDX-License-Identifier: Apache-2.0
//
// Source-visible startup barrier for phase-aligned multi-FPGA fabrics.
// Ready travels toward a root.  The root selects a future common epoch and
// broadcasts it back down the same tree.  Loss of local readiness after
// release is sticky and requires the board-level synchronous reset service.
module emuflow_runtime_sync_tree_node #(
    parameter integer EPOCH_BITS = 32,
    parameter integer CHILD_PORTS = 1,
    parameter [CHILD_PORTS-1:0] ACTIVE_CHILD_MASK = {CHILD_PORTS{1'b0}},
    parameter integer IS_ROOT = 0,
    parameter integer START_MARGIN_CYCLES = 16,
    parameter integer READY_STABLE_CYCLES = 4
) (
    input  wire                         fabric_clk,
    input  wire                         reset,
    input  wire                         local_ready,
    input  wire [CHILD_PORTS-1:0]       child_subtree_ready,
    input  wire                         parent_start_valid,
    input  wire [EPOCH_BITS-1:0]        parent_start_epoch,
    output wire                         subtree_ready,
    output wire                         child_start_valid,
    output wire [EPOCH_BITS-1:0]        child_start_epoch,
    output wire                         global_ready,
    output wire                         faulted,
    output reg  [EPOCH_BITS-1:0]        epoch
);
    reg [31:0] ready_stable_count;
    reg        start_armed;
    reg        released;
    reg        sticky_fault;
    reg [EPOCH_BITS-1:0] target_epoch;

    wire all_children_ready =
        &(child_subtree_ready | ~ACTIVE_CHILD_MASK);
    assign subtree_ready = local_ready && all_children_ready;
    assign child_start_valid = start_armed;
    assign child_start_epoch = target_epoch;
    assign global_ready = released && !sticky_fault;
    assign faulted = sticky_fault;

    initial begin
        if (EPOCH_BITS < 4)
            $error("EPOCH_BITS must be at least four");
        if (CHILD_PORTS < 1)
            $error("CHILD_PORTS must be at least one");
        if (START_MARGIN_CYCLES < 2)
            $error("START_MARGIN_CYCLES must be at least two");
        if (READY_STABLE_CYCLES < 1)
            $error("READY_STABLE_CYCLES must be positive");
    end

    always @(posedge fabric_clk) begin
        if (reset) begin
            epoch <= {EPOCH_BITS{1'b0}};
            ready_stable_count <= 0;
            start_armed <= 1'b0;
            released <= 1'b0;
            sticky_fault <= 1'b0;
            target_epoch <= {EPOCH_BITS{1'b0}};
        end else begin
            epoch <= epoch + {{(EPOCH_BITS-1){1'b0}}, 1'b1};

            if (subtree_ready) begin
                if (ready_stable_count < READY_STABLE_CYCLES)
                    ready_stable_count <= ready_stable_count + 1;
            end else begin
                ready_stable_count <= 0;
            end

            if (IS_ROOT != 0) begin
                if (!start_armed && subtree_ready &&
                    ready_stable_count >= READY_STABLE_CYCLES - 1) begin
                    target_epoch <= epoch + START_MARGIN_CYCLES;
                    start_armed <= 1'b1;
                end
            end else if (!start_armed && parent_start_valid) begin
                target_epoch <= parent_start_epoch;
                start_armed <= 1'b1;
            end

            if (start_armed && epoch == target_epoch)
                released <= 1'b1;
            // Include child readiness so a descendant failure propagates to
            // the root.  The physical control-plane binding must convert the
            // sticky tree fault into a synchronous global reset.
            if (released && !subtree_ready)
                sticky_fault <= 1'b1;
        end
    end
endmodule
