# Produce a post-synthesis Vivado checkpoint for the common TimingPathDB.

if {$argc != 6} {
  error "usage: analyze_timing.tcl PART MAPPED_VERILOG TOP TIMING_XDC OUTPUT_DIR EXPECTED_MAPPED_CELLS"
}
set part [lindex $argv 0]
set mapped_verilog [file normalize [lindex $argv 1]]
set top [lindex $argv 2]
set timing_xdc [file normalize [lindex $argv 3]]
set output_dir [file normalize [lindex $argv 4]]
set expected_cells [lindex $argv 5]
file mkdir $output_dir

create_project -in_memory -part $part
read_verilog $mapped_verilog
read_xdc $timing_xdc
synth_design -top $top -part $part -flatten_hierarchy none -mode out_of_context
set cells [get_cells -quiet -hier -filter {EMUFLOW_MAPPED == yes}]
if {[llength $cells] != $expected_cells} {
  error "post-synthesis timing design has [llength $cells] tagged mapped cells; expected $expected_cells"
}
set clocks [get_clocks -quiet]
if {[llength $clocks] == 0} {
  error "Vivado timing design has no constrained clocks"
}
foreach clock $clocks {
  if {[llength [get_ports -quiet -of_objects $clock]] != 1} {
    error "timing clock [get_property NAME $clock] is not bound to one design port"
  }
}
write_checkpoint -force "$output_dir/timing.dcp"
report_timing_summary -file "$output_dir/timing_summary.rpt"

set metrics [open "$output_dir/timing_metrics.tsv" w]
puts $metrics "metric\tvalue"
puts $metrics "vivado_version\t[version -short]"
puts $metrics "part\t$part"
puts $metrics "mapped_cells\t[llength $cells]"
puts $metrics "clocks\t[llength $clocks]"
close $metrics
puts "EMUFLOW_VIVADO_TIMING status=pass part=$part mapped_cells=[llength $cells] clocks=[llength $clocks]"
