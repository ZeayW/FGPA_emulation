# Synthetic hardware BSPs

Files in this directory contain explicit banks, package-pin identifiers, and
point-to-point channels for exercising EmuFlow's electrical pin-binding
algorithms before a physical board is selected.

They are algorithm-validation models, not manufacturable board definitions.
Synthetic package names must never be sourced into a hardware Vivado project.
Replace the model with a board-revision-controlled BSP and run vendor
electrical, timing, and DRC sign-off before claiming hardware closure.
