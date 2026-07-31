# Provider implementation for one mapped EmuFlow FPGA partition.
#
# Usage:
#   vivado -mode batch -source implement_partition.tcl -tclargs \
#     PART MAPPED_VERILOG TOP TIMING_XDC OUTPUT_DIR EXPECTED_MAPPED_CELLS \
#     DUT_PERIOD_NS \
#     ?PLACE_DIRECTIVE? ?ROUTE_DIRECTIVE?

if {$argc < 7 || $argc > 9} {
  error "usage: implement_partition.tcl PART MAPPED_VERILOG TOP TIMING_XDC OUTPUT_DIR EXPECTED_MAPPED_CELLS DUT_PERIOD_NS ?PLACE_DIRECTIVE? ?ROUTE_DIRECTIVE?"
}

set part [lindex $argv 0]
set mapped_verilog [file normalize [lindex $argv 1]]
set top [lindex $argv 2]
set timing_xdc [file normalize [lindex $argv 3]]
set output_dir [file normalize [lindex $argv 4]]
set expected_cells [lindex $argv 5]
set nominal_dut_period [lindex $argv 6]
set place_directive [expr {$argc >= 8 ? [lindex $argv 7] : "Default"}]
set route_directive [expr {$argc >= 9 ? [lindex $argv 8] : "Default"}]

if {![file isfile $mapped_verilog]} {
  error "mapped Verilog does not exist: $mapped_verilog"
}
if {![file isfile $timing_xdc]} {
  error "timing XDC does not exist: $timing_xdc"
}
if {![string is integer -strict $expected_cells] || $expected_cells < 0} {
  error "EXPECTED_MAPPED_CELLS must be a non-negative integer"
}
if {![string is double -strict $nominal_dut_period] || $nominal_dut_period <= 0.0} {
  error "DUT_PERIOD_NS must be a positive number"
}
file mkdir $output_dir

create_project -in_memory -part $part
read_verilog $mapped_verilog
read_xdc $timing_xdc
synth_design -top $top -part $part -flatten_hierarchy none -mode out_of_context

set logical_objects [get_cells -quiet -hier -filter {EMUFLOW_MAPPED == yes}]
if {[llength $logical_objects] != $expected_cells} {
  error "post-synthesis design has [llength $logical_objects] tagged mapped cells; expected $expected_cells"
}
set mapped_objects [get_cells -quiet -hier -filter {REF_NAME != GND && REF_NAME != VCC}]
set mapped_names [lsort -ascii [get_property NAME $mapped_objects]]
set mapped_inventory [open "$output_dir/mapped_cells.tsv" w]
puts $mapped_inventory "name\tref_name"
foreach object $mapped_objects {
  puts $mapped_inventory "[get_property NAME $object]\t[get_property REF_NAME $object]"
}
close $mapped_inventory
write_checkpoint -force "$output_dir/synthesized.dcp"

opt_design
place_design -directive $place_directive
write_checkpoint -force "$output_dir/placed.dcp"
route_design -directive $route_directive
write_checkpoint -force "$output_dir/routed.dcp"

report_route_status -file "$output_dir/route_status.rpt"
report_drc -file "$output_dir/drc.rpt"
report_timing_summary -file "$output_dir/timing_summary.rpt"
report_utilization -hierarchical -file "$output_dir/utilization.rpt"

set routed_objects [get_cells -quiet -hier -filter {REF_NAME != GND && REF_NAME != VCC}]
array set routed_ref_by_name {}
foreach object $routed_objects {
  set name [get_property NAME $object]
  if {[info exists routed_ref_by_name($name)]} {
    error "routed checkpoint contains duplicate cell $name"
  }
  set routed_ref_by_name($name) [get_property REF_NAME $object]
}
foreach name $mapped_names {
  if {![info exists routed_ref_by_name($name)]} {
    error "mapped cell $name is missing from routed checkpoint"
  }
  unset routed_ref_by_name($name)
}

set infrastructure_cells 0
set infrastructure_inventory [open "$output_dir/infrastructure_cells.tsv" w]
puts $infrastructure_inventory "name\tref_name\tclass"
foreach name [lsort -ascii [array names routed_ref_by_name]] {
  set ref_name $routed_ref_by_name($name)
  if {![string match "BUFG*" $ref_name] &&
      ![string match "IBUF*" $ref_name] &&
      ![string match "OBUF*" $ref_name]} {
    error "unapproved physical infrastructure cell $name has type $ref_name"
  }
  puts $infrastructure_inventory "$name\t$ref_name\tclock_or_io"
  incr infrastructure_cells
}
close $infrastructure_inventory

set unrouted [get_nets -quiet -filter {ROUTE_STATUS == UNROUTED}]
set drc_all [get_drc_violations -quiet]
set drc_violations {}
set drc_warnings 0
foreach violation $drc_all {
  set severity [get_property SEVERITY $violation]
  if {$severity eq "Error" || $severity eq "Critical Warning"} {
    lappend drc_violations $violation
  } else {
    incr drc_warnings
  }
}
if {[llength $unrouted] != 0} {
  error "implementation has [llength $unrouted] unrouted nets"
}
if {[llength $drc_violations] != 0} {
  error "implementation has [llength $drc_violations] DRC violations"
}

set dut_clocks [get_clocks -quiet emuflow_dut_clk]
set fabric_clocks [get_clocks -quiet emuflow_fabric_clk]
if {[llength $dut_clocks] > 1 || [llength $fabric_clocks] != 1} {
  error "expected at most one DUT clock and exactly one fabric clock"
}
set fabric_clock_ports [get_ports -quiet -of_objects $fabric_clocks]
if {[llength $fabric_clock_ports] != 1} {
  error "fabric clock must be bound to exactly one design port"
}
set fabric_period [get_property PERIOD $fabric_clocks]
if {[llength $dut_clocks] == 1} {
  set dut_clock_ports [get_ports -quiet -of_objects $dut_clocks]
  if {[llength $dut_clock_ports] != 1} {
    error "DUT clock must be bound to exactly one design port"
  }
  set dut_period [get_property PERIOD $dut_clocks]
} else {
  set dut_period $nominal_dut_period
}

proc emuflow_path_metrics {from_clock to_clock period} {
  set paths [get_timing_paths -quiet -max_paths 1 -nworst 1 \
    -from $from_clock -to $to_clock]
  if {[llength $paths] == 0} {
    return [list $period 0.0 0]
  }
  set path [lindex $paths 0]
  return [list \
    [get_property SLACK $path] \
    [get_property DATAPATH_DELAY $path] \
    1]
}

set fabric_metrics [emuflow_path_metrics \
  $fabric_clocks $fabric_clocks $fabric_period]
if {[llength $dut_clocks] == 0} {
  set dut_metrics [list $dut_period 0.0 0]
  set cross_slack $dut_period
  set cross_delay 0.0
  set cross_present 0
} else {
  set dut_metrics [emuflow_path_metrics $dut_clocks $dut_clocks $dut_period]
  set cross_paths [get_timing_paths -quiet -max_paths 1 -nworst 1 \
    -from $fabric_clocks -to $dut_clocks]
  if {[llength $cross_paths] == 0} {
    set cross_slack $dut_period
    set cross_delay 0.0
    set cross_present 0
  } else {
    set cross_path [lindex $cross_paths 0]
    set cross_slack [get_property SLACK $cross_path]
    set cross_delay [get_property DATAPATH_DELAY $cross_path]
    set cross_present 1
  }
}
set dut_slack [lindex $dut_metrics 0]
set dut_delay [lindex $dut_metrics 1]
set dut_present [lindex $dut_metrics 2]
set fabric_slack [lindex $fabric_metrics 0]
set fabric_delay [lindex $fabric_metrics 1]
set fabric_present [lindex $fabric_metrics 2]
set wns [expr {min($dut_slack, min($fabric_slack, $cross_slack))}]
if {$wns < 0.0} {
  error "implementation timing failed with WNS $wns ns"
}
set critical_path [expr {max($dut_delay, max($fabric_delay, $cross_delay))}]
set physical_cells [llength $routed_objects]
set dsp48_cells [llength [get_cells -quiet -hier -filter {REF_NAME =~ DSP48*}]]
set ramb18_cells [llength [get_cells -quiet -hier -filter {REF_NAME =~ RAMB18*}]]
set ramb36_cells [llength [get_cells -quiet -hier -filter {REF_NAME =~ RAMB36*}]]
set optimization_cells [expr {
  $physical_cells - $expected_cells - $infrastructure_cells
}]
if {$optimization_cells < 0} {
  error "physical cell accounting is smaller than mapped logical coverage"
}

set metrics [open "$output_dir/implementation_metrics.tsv" w]
puts $metrics "metric\tvalue"
puts $metrics "vivado_version\t[version -short]"
puts $metrics "part\t$part"
puts $metrics "mapped_cells\t$expected_cells"
puts $metrics "physical_cells\t$physical_cells"
puts $metrics "infrastructure_cells\t$infrastructure_cells"
puts $metrics "optimization_cells\t$optimization_cells"
puts $metrics "dsp48_cells\t$dsp48_cells"
puts $metrics "ramb18_cells\t$ramb18_cells"
puts $metrics "ramb36_cells\t$ramb36_cells"
puts $metrics "nets\t[llength [get_nets -quiet]]"
puts $metrics "unrouted_nets\t0"
puts $metrics "drc_violations\t0"
puts $metrics "drc_warnings\t$drc_warnings"
puts $metrics "dut_period_ns\t$dut_period"
puts $metrics "fabric_period_ns\t$fabric_period"
puts $metrics "wns_ns\t$wns"
puts $metrics "critical_path_ns\t$critical_path"
puts $metrics "dut_wns_ns\t$dut_slack"
puts $metrics "dut_delay_ns\t$dut_delay"
puts $metrics "dut_path_present\t$dut_present"
puts $metrics "fabric_wns_ns\t$fabric_slack"
puts $metrics "fabric_delay_ns\t$fabric_delay"
puts $metrics "fabric_path_present\t$fabric_present"
puts $metrics "fabric_to_dut_wns_ns\t$cross_slack"
puts $metrics "fabric_to_dut_delay_ns\t$cross_delay"
puts $metrics "fabric_to_dut_path_present\t$cross_present"
close $metrics

puts "EMUFLOW_VIVADO_BACKEND status=pass part=$part mapped_cells=$expected_cells physical_cells=$physical_cells infrastructure_cells=$infrastructure_cells optimization_cells=$optimization_cells dsp48_cells=$dsp48_cells ramb18_cells=$ramb18_cells ramb36_cells=$ramb36_cells unrouted_nets=0 drc_violations=0 drc_warnings=$drc_warnings wns_ns=$wns"
