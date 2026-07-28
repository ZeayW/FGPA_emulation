if {$argc < 3 || $argc > 4} {
    error "usage: vivado -mode batch -source reroute_checkpoint.tcl -tclargs PLACED_DCP OUTPUT_DIR ROUTE_DIRECTIVE ?PHYS_OPT_DIRECTIVE?"
}

set checkpoint [file normalize [lindex $argv 0]]
set output_dir [file normalize [lindex $argv 1]]
set route_directive [lindex $argv 2]
set phys_opt_directive none
if {$argc == 4} {
    set phys_opt_directive [lindex $argv 3]
}

if {![file isfile $checkpoint]} {
    error "placed checkpoint does not exist: $checkpoint"
}
file mkdir $output_dir

open_checkpoint $checkpoint
set cells [get_cells -hier -quiet -filter {REF_NAME != GND && REF_NAME != VCC}]
set unplaced [get_cells -hier -quiet -filter {
    IS_PRIMITIVE && STATUS == UNPLACED && REF_NAME != GND && REF_NAME != VCC
}]
if {[llength $unplaced] != 0} {
    # Out-of-context checkpoints can retain unplaced boundary clock/IO
    # primitives even after place_design.  The original route_design accepts
    # these objects, so record their identity without rejecting a reusable
    # checkpoint before the router gets a chance to validate it.
    foreach cell $unplaced {
        puts "EMUFLOW_REROUTE_UNPLACED_BOUNDARY cell=[get_property NAME $cell] ref=[get_property REF_NAME $cell]"
    }
}

puts "EMUFLOW_REROUTE_START cells=[llength $cells] route=$route_directive phys_opt=$phys_opt_directive checkpoint=$checkpoint"
report_design_analysis -congestion -file "$output_dir/pre_route_congestion.rpt"
if {$phys_opt_directive ne "none"} {
    phys_opt_design -directive $phys_opt_directive
    write_checkpoint -force "$output_dir/phys_opt.dcp"
}

set route_failed [catch {
    route_design -directive $route_directive
} route_message route_options]

if {$route_failed} {
    write_checkpoint -force "$output_dir/failed_route.dcp"
} else {
    write_checkpoint -force "$output_dir/routed.dcp"
}
report_route_status -file "$output_dir/route_status.rpt"
report_drc -file "$output_dir/drc.rpt"
report_timing_summary -file "$output_dir/timing_summary.rpt"

set unrouted [get_nets -hier -quiet -filter {ROUTE_STATUS == UNROUTED}]
if {$route_failed} {
    puts "EMUFLOW_REROUTE status=fail route=$route_directive phys_opt=$phys_opt_directive unrouted=[llength $unrouted] message={$route_message}"
    return -options $route_options $route_message
}
if {[llength $unrouted] != 0} {
    error "route completed with [llength $unrouted] unrouted nets"
}
puts "EMUFLOW_REROUTE status=pass route=$route_directive phys_opt=$phys_opt_directive cells=[llength $cells] routed_dcp=$output_dir/routed.dcp"
