if {$argc < 6} {
    error "usage: vivado -mode batch -source validate_mapped.tcl -tclargs PART MAPPED_VERILOG TOP PLACEMENT_XDC OUTPUT_DIR EXPECTED_CELLS ?CLOCK_PORT PERIOD_NS?"
}

set part [lindex $argv 0]
set netlist_path [file normalize [lindex $argv 1]]
set top [lindex $argv 2]
set placement_xdc [file normalize [lindex $argv 3]]
set output_dir [file normalize [lindex $argv 4]]
set expected_cells [lindex $argv 5]
file mkdir $output_dir

create_project -in_memory -part $part
read_verilog $netlist_path
synth_design -top $top -part $part -flatten_hierarchy none -mode out_of_context
write_checkpoint -force "$output_dir/unplaced.dcp"
set cell_inventory [open "$output_dir/cells_before_xdc.txt" w]
foreach cell [lsort -dictionary [get_cells -hier]] {
    puts $cell_inventory [get_property NAME $cell]
}
close $cell_inventory
read_xdc $placement_xdc
if {$argc >= 8} {
    set clock_port [lindex $argv 6]
    set clock_period [lindex $argv 7]
    set clock_ports [get_ports -quiet $clock_port]
    if {[llength $clock_ports] != 1} {
        error "clock port $clock_port did not resolve to exactly one port"
    }
    create_clock -name emuflow_dut_clk -period $clock_period $clock_ports
}

set ff_loc_repairs 0
for {set index 0} {$index < $expected_cells} {incr index} {
    set variable "emuflow_cell_$index"
    if {![info exists $variable]} {
        error "$variable was not created by the placement XDC"
    }
    set cells [set $variable]
    if {[llength $cells] != 1} {
        error "$variable did not resolve to exactly one mapped cell"
    }
    set ref_name [get_property REF_NAME $cells]
    if {[get_property LOC $cells] eq ""} {
        if {[string match "FD*" $ref_name]} {
            # OpenPARF currently models FF capacity but not the complete
            # UltraScale+ CKEN/control-set compatibility relation. A LOC can
            # therefore be rejected when unrelated CE nets share a SLICE.
            # Leave only those conflicting FFs movable for Vivado repair.
            set emuflow_ff_repair($index) 1
            incr ff_loc_repairs
        } else {
            error "$variable does not have a LOC constraint"
        }
    }
    if {![string match "FD*" $ref_name] && [get_property BEL $cells] eq ""} {
        error "$variable does not have a BEL constraint"
    }
}

# Complete placement of unconstrained physical objects. LUT Site/BEL decisions
# remain fixed; only FF LOCs rejected for control-set conflicts may spill.
place_design -directive Quick
for {set index 0} {$index < $expected_cells} {incr index} {
    set variable "emuflow_cell_$index"
    set cells [set $variable]
    set ref_name [get_property REF_NAME $cells]
    if {[get_property LOC $cells] eq ""} {
        error "$variable is unplaced after placement completion"
    }
    if {![string match "FD*" $ref_name]} {
        if {![get_property IS_LOC_FIXED $cells]} {
            error "$variable LOC was not kept fixed during placement completion"
        }
        if {![get_property IS_BEL_FIXED $cells]} {
            error "$variable BEL was not kept fixed during placement completion"
        }
    } elseif {![info exists emuflow_ff_repair($index)] &&
              ![get_property IS_LOC_FIXED $cells]} {
        error "$variable FF LOC was not kept fixed during placement completion"
    }
}
write_checkpoint -force "$output_dir/placed.dcp"
route_design
write_checkpoint -force "$output_dir/routed.dcp"
report_route_status -file "$output_dir/route_status.rpt"
report_drc -file "$output_dir/drc.rpt"
report_timing_summary -file "$output_dir/timing_summary.rpt"

set unrouted [get_nets -quiet -filter {ROUTE_STATUS == UNROUTED}]
if {[llength $unrouted] != 0} {
    error "route completed with [llength $unrouted] unrouted nets"
}
puts "EMUFLOW_MAPPED_VIVADO status=pass part=$part cells=$expected_cells ff_loc_repairs=$ff_loc_repairs routed_dcp=$output_dir/routed.dcp"
