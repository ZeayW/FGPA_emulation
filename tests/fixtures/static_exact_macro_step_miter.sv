// Canonical depth-one static-exact macro-step miter.
//
// This is deliberately an unrolled one-commit proof fixture, not a claim of
// general-design formal closure.  It models the directed regression used by
// tests/test_combinational_cut.py: q0 -> local LUT -> cut n0 -> two local LUTs
// -> cut d -> q1.  Both LUT cones are buffers.  The stale transport state is
// unconstrained and must not affect the committed architectural state.
module static_exact_macro_step_miter;
  (* anyconst *) reg q0;
  (* anyconst *) reg q1;
  (* anyconst *) reg stale_shadow_n0;
  (* anyconst *) reg stale_shadow_d;

  wire reference_q0_next = q0;
  wire reference_q1_next = q0;

  // Unrolled distributed macro-cycle.  Values are sampled only after their
  // source-ready edge and each downstream stage consumes the current-frame
  // arrival, never the unconstrained stale shadows above.
  wire slot_1_tx_n0 = q0;
  wire slot_2_rx_n0 = slot_1_tx_n0;
  wire slot_4_tx_d = slot_2_rx_n0;
  wire slot_5_rx_d = slot_4_tx_d;
  wire commit_q0_next = q0;
  wire commit_q1_next = slot_5_rx_d;

  always @* begin
    assert(commit_q0_next == reference_q0_next);
    assert(commit_q1_next == reference_q1_next);
  end

  // Keep the implementation-state variables semantically present so a
  // vacuous optimization cannot turn this into a reset-only proof.
  wire _unused_ok = stale_shadow_n0 ^ stale_shadow_d ^ q1;
endmodule
