# Corundum Ethernet RTL provenance

This directory contains the minimal 10GBASE-R PCS source closure copied from
[Corundum](https://github.com/corundum/corundum), including its `verilog-ethernet`
subtree, at commit `1ca0151b97af85aa5dd306d74b6bcec65904d2ce`.

Imported PCS paths originate under `fpga/lib/eth/rtl/`. The asynchronous FIFO
and its license originate under `fpga/lib/eth/lib/axis/`. The files are
retained without functional modification and remain under their upstream MIT
licenses. EmuFlow-specific deterministic slot release and runtime-sync control
adapters live outside this directory.
