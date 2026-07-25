if {$argc < 4} {
    error "usage: vivado -mode batch -source validate_phase2.tcl -tclargs PART RTL PLACEMENT_XDC OUTPUT_DIR"
}

set part [lindex $argv 0]
set rtl_path [file normalize [lindex $argv 1]]
set placement_xdc [file normalize [lindex $argv 2]]
set output_dir [file normalize [lindex $argv 3]]
file mkdir $output_dir

create_project -in_memory -part $part
read_verilog $rtl_path
synth_design -top phase2_primitives -part $part -flatten_hierarchy none
write_checkpoint -force "$output_dir/unplaced.dcp"
read_xdc $placement_xdc

for {set index 0} {$index < 8} {incr index} {
    set variable "emuflow_cell_$index"
    set cells [set $variable]
    if {[llength $cells] != 1} {
        error "$variable did not resolve to exactly one mapped cell"
    }
    if {[get_property LOC $cells] eq "" || [get_property BEL $cells] eq ""} {
        error "$variable does not have both LOC and BEL constraints"
    }
}

# Vivado still performs mandatory IO/site-pin completion. The eight OpenPARF
# logic cells are fixed by LOC/BEL and checked again after this command.
place_design -directive Quick
for {set index 0} {$index < 8} {incr index} {
    set variable "emuflow_cell_$index"
    set cells [set $variable]
    if {![get_property IS_LOC_FIXED $cells] || ![get_property IS_BEL_FIXED $cells]} {
        error "$variable was not kept fixed during placement completion"
    }
}
write_checkpoint -force "$output_dir/placed.dcp"
route_design
write_checkpoint -force "$output_dir/routed.dcp"
report_route_status -file "$output_dir/route_status.rpt"
report_drc -file "$output_dir/drc.rpt"

set unrouted [get_nets -quiet -filter {ROUTE_STATUS == UNROUTED}]
if {[llength $unrouted] != 0} {
    error "route completed with [llength $unrouted] unrouted nets"
}
puts "EMUFLOW_PHASE2_VIVADO status=pass part=$part routed_dcp=$output_dir/routed.dcp"
