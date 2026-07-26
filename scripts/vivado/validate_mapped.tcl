if {$argc < 6} {
    error "usage: vivado -mode batch -source validate_mapped.tcl -tclargs PART MAPPED_VERILOG TOP PLACEMENT_CONSTRAINTS OUTPUT_DIR EXPECTED_CELLS ?CLOCK_PORT PERIOD_NS?"
}

set part [lindex $argv 0]
set netlist_path [file normalize [lindex $argv 1]]
set top [lindex $argv 2]
set placement_constraints [file normalize [lindex $argv 3]]
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
if {[file extension $placement_constraints] eq ".tsv"} {
    set placement_start_ms [clock milliseconds]
    set cells_by_name [dict create]
    set all_cells [get_cells -hier]
    set all_names [get_property NAME $all_cells]
    foreach cell $all_cells name $all_names {
        if {[dict exists $cells_by_name $name]} {
            error "duplicate mapped cell name $name"
        }
        dict set cells_by_name $name $cell
    }

    set placement_input [open $placement_constraints r]
    set placement_count 0
    set cells_by_site [dict create]
    set cells_by_bel [dict create]
    while {[gets $placement_input line] >= 0} {
        if {$line eq "" || [string index $line 0] eq "#"} {
            continue
        }
        set fields [split $line "\t"]
        if {[llength $fields] != 5} {
            error "malformed placement TSV row: $line"
        }
        set index [lindex $fields 0]
        if {$index != $placement_count} {
            error "placement TSV index $index; expected $placement_count"
        }
        set name [encoding convertfrom utf-8 \
            [binary decode hex [lindex $fields 1]]]
        if {![dict exists $cells_by_name $name]} {
            error "placement TSV cell $name does not exist in mapped design"
        }
        set variable "emuflow_cell_$index"
        set cell [dict get $cells_by_name $name]
        set $variable $cell
        dict lappend cells_by_site [lindex $fields 2] $cell
        set cell_type [lindex $fields 4]
        if {![string match "FD*" $cell_type]} {
            dict lappend cells_by_bel [lindex $fields 3] $cell
        }
        incr placement_count
    }
    close $placement_input
    if {$placement_count != $expected_cells} {
        error "placement TSV has $placement_count cells; expected $expected_cells"
    }
    dict for {site cells} $cells_by_site {
        set_property LOC $site $cells
    }
    dict for {bel cells} $cells_by_bel {
        set_property BEL $bel $cells
    }
    set placement_elapsed_ms [expr {[clock milliseconds] - $placement_start_ms}]
    puts "EMUFLOW_PLACEMENT_TSV status=pass cells=$placement_count elapsed_ms=$placement_elapsed_ms"
} else {
    read_xdc $placement_constraints
}
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
