# EmuFlow

EmuFlow is an open, board-abstracted multi-FPGA emulation flow targeting AMD
UltraScale+ devices. The long-term flow covers logic synthesis, partitioning,
board-level routing, TDM scheduling, lane/pin assignment, OpenPARF placement,
FPGA routing, and vendor-assisted bitstream generation.

The repository currently implements **Phase 1: board-independent frontend**:

- versioned EmuIR and Virtual BoardDB formats;
- strict validation without third-party Python dependencies;
- Yosys JSON to EmuIR import;
- UltraScale+ primitive resource classification;
- a runnable Phase 1 pipeline and machine-readable report;
- a virtual two-FPGA `xcvu3p` reference platform.

See [docs/FLOW_PLAN.md](docs/FLOW_PLAN.md) for the complete architecture,
phase boundaries, artifacts, and acceptance criteria.

## Quick start

The checked-in Yosys fixture lets Phase 1 run even when Yosys is not installed:

```bash
PYTHONPATH=src python3 -m emuflow phase1 \
  --yosys-json examples/yosys/counter.json \
  --top counter \
  --clock clk \
  --platform platforms/virtual/xcvu3p_2fpga_p2p.json \
  --out build/phase1-demo
```

Inspect the generated design:

```bash
PYTHONPATH=src python3 -m emuflow ir stats \
  build/phase1-demo/design.emuir.json
```

Validate the virtual platform:

```bash
PYTHONPATH=src python3 -m emuflow platform validate \
  platforms/virtual/xcvu3p_2fpga_p2p.json
```

Run the tests:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## Using a real Yosys installation

Phase 1 can invoke Yosys directly when it is installed:

```bash
PYTHONPATH=src python3 -m emuflow synth-yosys \
  examples/rtl/counter.v \
  --top counter \
  --family xcup \
  --output build/counter.json \
  --log build/counter-yosys.log
```

Then replace `examples/yosys/counter.json` in the Phase 1 command with
`build/counter.json`.

The first phase deliberately stops at the logical IR. UltraScale+ site packing,
FPGA Interchange conversion, OpenPARF placement, and DCP generation are
subsequent phases described in the flow plan.
