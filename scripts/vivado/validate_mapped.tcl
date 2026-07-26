if {$argc < 6} {
    error "usage: vivado -mode batch -source validate_mapped.tcl -tclargs PART MAPPED_VERILOG TOP PLACEMENT_CONSTRAINTS OUTPUT_DIR EXPECTED_CELLS ?CLOCK_PORT PERIOD_NS? ?EXTRA_XDC?"
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
    set all_cells [lsort -ascii [get_cells -hier -filter \
        {REF_NAME != GND && REF_NAME != VCC}]]
    set all_names [get_property NAME $all_cells]
    if {[llength $all_cells] != $expected_cells} {
        error "mapped design has [llength $all_cells] cells; expected $expected_cells"
    }

    set placement_input [open $placement_constraints r]
    set placement_lines [split [read $placement_input] "\n"]
    close $placement_input
    # The first row is a header and the final split item is empty because the
    # generated TSV ends with a newline.
    set placement_rows [lrange $placement_lines 1 end-1]
    if {[llength $placement_rows] != $expected_cells} {
        error "placement TSV has [llength $placement_rows] cells; expected $expected_cells"
    }
    set placement_count 0
    array set lut_cells_by_site {}
    array set ff_cells_by_site {}
    array set cells_by_bel {}
    foreach cell $all_cells actual_name $all_names line $placement_rows {
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
        if {$name ne $actual_name} {
            error "placement TSV cell $name does not match mapped cell $actual_name at index $index"
        }
        set variable "emuflow_cell_$index"
        set $variable $cell
        set cell_type [lindex $fields 4]
        if {[string match "FD*" $cell_type]} {
            lappend ff_cells_by_site([lindex $fields 2]) $cell
        } else {
            lappend lut_cells_by_site([lindex $fields 2]) $cell
            lappend cells_by_bel([lindex $fields 3]) $cell
        }
        incr placement_count
    }
    foreach bel [array names cells_by_bel] {
        set_property BEL $bel $cells_by_bel($bel)
    }
    foreach site [array names lut_cells_by_site] {
        set_property LOC $site $lut_cells_by_site($site)
    }
    # A rejected FF LOC is an expected signal from the incomplete OpenPARF
    # control-set model, not a fatal implementation error. Keep Tcl's error
    # return for catch-based repair, but do not poison route_design's global
    # message state with an ERROR severity.
    set_msg_config -id {Vivado 12-1410} -new_severity WARNING
    set initial_ff_loc_rejects 0
    foreach site [array names ff_cells_by_site] {
        if {[catch {set_property LOC $site $ff_cells_by_site($site)}]} {
            foreach cell $ff_cells_by_site($site) {
                if {[catch {set_property LOC $site $cell}]} {
                    incr initial_ff_loc_rejects
                }
            }
        }
    }
    set placement_elapsed_ms [expr {[clock milliseconds] - $placement_start_ms}]
    puts "EMUFLOW_PLACEMENT_TSV status=pass cells=$placement_count initial_ff_loc_rejects=$initial_ff_loc_rejects elapsed_ms=$placement_elapsed_ms"
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
if {$argc >= 9} {
    set extra_xdc [file normalize [lindex $argv 8]]
    if {![file isfile $extra_xdc]} {
        error "extra XDC does not exist: $extra_xdc"
    }
    read_xdc $extra_xdc
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
