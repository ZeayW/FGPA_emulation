if {$argc != 3} {
    error "usage: vivado -mode batch -source report_runtime_contract.tcl -tclargs ROUTED_DCP OUTPUT_DIR EXPECTED_CELLS"
}

set checkpoint [file normalize [lindex $argv 0]]
set output_dir [file normalize [lindex $argv 1]]
set expected_cells [lindex $argv 2]
file mkdir $output_dir
open_checkpoint $checkpoint

set cells [get_cells -hier -filter {REF_NAME != GND && REF_NAME != VCC}]
if {[llength $cells] != $expected_cells} {
    error "routed checkpoint has [llength $cells] cells; expected $expected_cells"
}
set dut_clocks [get_clocks -quiet emuflow_dut_clk]
set fabric_clocks [get_clocks -quiet emuflow_fabric_clk]
if {[llength $dut_clocks] != 1} {
    error "expected exactly one emuflow_dut_clk"
}
if {[llength $fabric_clocks] != 1} {
    error "expected exactly one emuflow_fabric_clk"
}
set dut_period [get_property PERIOD $dut_clocks]
set fabric_period [get_property PERIOD $fabric_clocks]

report_route_status -file "$output_dir/route_status.rpt"
report_drc -file "$output_dir/drc.rpt"
report_timing_summary -file "$output_dir/timing_summary.rpt"
report_clock_interaction -file "$output_dir/clock_interaction.rpt"

set unrouted [get_nets -quiet -filter {ROUTE_STATUS == UNROUTED}]
set drc_violations [get_drc_violations -quiet]
if {[llength $unrouted] != 0} {
    error "runtime checkpoint has [llength $unrouted] unrouted nets"
}
if {[llength $drc_violations] != 0} {
    error "runtime checkpoint has [llength $drc_violations] DRC violations"
}
set timing_paths [get_timing_paths -quiet -max_paths 1 -nworst 1]
if {[llength $timing_paths] != 1} {
    error "runtime checkpoint did not produce a worst timing path"
}
set wns [get_property SLACK $timing_paths]
if {$wns < 0.0} {
    error "runtime timing failed with WNS $wns ns"
}
proc emuflow_clock_pair_wns {from_clock to_clock label} {
    set paths [get_timing_paths -quiet -max_paths 1 -nworst 1 \
        -from $from_clock -to $to_clock]
    if {[llength $paths] != 1} {
        error "$label did not produce a worst timing path"
    }
    set slack [get_property SLACK $paths]
    if {$slack < 0.0} {
        error "$label timing failed with WNS $slack ns"
    }
    return $slack
}
set dut_wns [emuflow_clock_pair_wns \
    $dut_clocks $dut_clocks "DUT clock"]
set fabric_wns [emuflow_clock_pair_wns \
    $fabric_clocks $fabric_clocks "fabric clock"]
set fabric_to_dut_wns [emuflow_clock_pair_wns \
    $fabric_clocks $dut_clocks "fabric-to-DUT stable-data window"]

set metrics [open "$output_dir/runtime_metrics.tsv" w]
puts $metrics "metric\tvalue"
puts $metrics "cells\t[llength $cells]"
puts $metrics "nets\t[llength [get_nets]]"
puts $metrics "ports\t[llength [get_ports]]"
puts $metrics "unrouted_nets\t[llength $unrouted]"
puts $metrics "drc_violations\t[llength $drc_violations]"
puts $metrics "dut_period_ns\t$dut_period"
puts $metrics "fabric_period_ns\t$fabric_period"
puts $metrics "wns_ns\t$wns"
puts $metrics "dut_wns_ns\t$dut_wns"
puts $metrics "fabric_wns_ns\t$fabric_wns"
puts $metrics "fabric_to_dut_wns_ns\t$fabric_to_dut_wns"
close $metrics

puts "EMUFLOW_RUNTIME_VIVADO status=pass cells=[llength $cells] unrouted_nets=0 drc_violations=0 dut_period_ns=$dut_period fabric_period_ns=$fabric_period wns_ns=$wns dut_wns_ns=$dut_wns fabric_wns_ns=$fabric_wns fabric_to_dut_wns_ns=$fabric_to_dut_wns"
