if {$argc < 6} {
    error "usage: vivado -mode batch -source validate_mapped.tcl -tclargs PART MAPPED_VERILOG_OR_DCP TOP PLACEMENT_CONSTRAINTS OUTPUT_DIR EXPECTED_CELLS ?CLOCK_PORT PERIOD_NS? ?EXTRA_XDC? ?MAX_FIXED_LUTS_PER_SITE? ?PLACE_DIRECTIVE? ?ROUTE_DIRECTIVE? ?ANCHOR_SITE_MODULUS?"
}

set part [lindex $argv 0]
set netlist_path [file normalize [lindex $argv 1]]
set top [lindex $argv 2]
set placement_constraints [file normalize [lindex $argv 3]]
set output_dir [file normalize [lindex $argv 4]]
set expected_cells [lindex $argv 5]
file mkdir $output_dir

if {[file extension $netlist_path] eq ".dcp"} {
    open_checkpoint $netlist_path
} else {
    create_project -in_memory -part $part
    read_verilog $netlist_path
    synth_design -top $top -part $part -flatten_hierarchy none -mode out_of_context
}
set output_unplaced [file normalize "$output_dir/unplaced.dcp"]
if {$netlist_path ne $output_unplaced} {
    write_checkpoint -force $output_unplaced
}
set cell_inventory [open "$output_dir/cells_before_xdc.txt" w]
foreach cell [lsort -dictionary [get_cells -hier]] {
    puts $cell_inventory [get_property NAME $cell]
}
close $cell_inventory
set fixed_lut_anchors 0
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
    array set fixed_luts_by_site {}
    set max_fixed_luts_per_site -1
    if {$argc >= 10} {
        set max_fixed_luts_per_site [lindex $argv 9]
        if {$max_fixed_luts_per_site < 0} {
            error "MAX_FIXED_LUTS_PER_SITE must be non-negative"
        }
    }
    set anchor_site_modulus 1
    if {$argc >= 13} {
        set anchor_site_modulus [lindex $argv 12]
        if {$anchor_site_modulus <= 0} {
            error "ANCHOR_SITE_MODULUS must be positive"
        }
    }
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
            if {$max_fixed_luts_per_site >= 0} {
                set emuflow_ff_repair($index) 1
            } else {
                lappend ff_cells_by_site([lindex $fields 2]) $cell
            }
        } else {
            set site [lindex $fields 2]
            if {![info exists fixed_luts_by_site($site)]} {
                set fixed_luts_by_site($site) 0
            }
            set anchor_site 1
            if {$anchor_site_modulus > 1} {
                if {![regexp {^SLICE_X([0-9]+)Y([0-9]+)$} \
                    $site unused site_x site_y]} {
                    error "cannot subsample non-SLICE anchor site $site"
                }
                set anchor_site [expr {
                    (($site_x * 131) + ($site_y * 17)) %
                    $anchor_site_modulus == 0
                }]
            }
            if {$anchor_site &&
                ($max_fixed_luts_per_site < 0 ||
                 $fixed_luts_by_site($site) < $max_fixed_luts_per_site)} {
                lappend lut_cells_by_site($site) $cell
                lappend cells_by_bel([lindex $fields 3]) $cell
                incr fixed_luts_by_site($site)
                incr fixed_lut_anchors
            } else {
                set emuflow_lut_repair($index) 1
            }
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
    puts "EMUFLOW_PLACEMENT_TSV status=pass cells=$placement_count fixed_lut_anchors=$fixed_lut_anchors anchor_site_modulus=$anchor_site_modulus initial_ff_loc_rejects=$initial_ff_loc_rejects elapsed_ms=$placement_elapsed_ms"
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
        } elseif {[info exists emuflow_lut_repair($index)]} {
            # Coarse OpenPARF guidance fixes only the selected per-site LUT
            # anchors. Vivado is expected to place all remaining LUTs.
        } else {
            error "$variable does not have a LOC constraint"
        }
    }
    if {![string match "FD*" $ref_name] &&
        ![info exists emuflow_lut_repair($index)] &&
        [get_property BEL $cells] eq ""} {
        error "$variable does not have a BEL constraint"
    }
}

# Complete placement of unconstrained physical objects. In exact mode every
# accepted Site/BEL remains fixed and only rejected FF LOCs may spill. In
# coarse-anchor mode Vivado places all non-anchor LUTs and FFs. Allow an SSI
# congestion-aware directive without changing the exact identity/anchor gate.
set place_directive Quick
if {$argc >= 11} {
    set place_directive [lindex $argv 10]
}
set route_directive Default
if {$argc >= 12} {
    set route_directive [lindex $argv 11]
}
puts "EMUFLOW_IMPLEMENTATION_DIRECTIVES place=$place_directive route=$route_directive"
place_design -directive $place_directive
for {set index 0} {$index < $expected_cells} {incr index} {
    set variable "emuflow_cell_$index"
    set cells [set $variable]
    set ref_name [get_property REF_NAME $cells]
    if {[get_property LOC $cells] eq ""} {
        error "$variable is unplaced after placement completion"
    }
    if {![string match "FD*" $ref_name]} {
        if {![info exists emuflow_lut_repair($index)] &&
            ![get_property IS_LOC_FIXED $cells]} {
            error "$variable LOC was not kept fixed during placement completion"
        }
        if {![info exists emuflow_lut_repair($index)] &&
            ![get_property IS_BEL_FIXED $cells]} {
            error "$variable BEL was not kept fixed during placement completion"
        }
    } elseif {![info exists emuflow_ff_repair($index)] &&
              ![get_property IS_LOC_FIXED $cells]} {
        error "$variable FF LOC was not kept fixed during placement completion"
    }
}
write_checkpoint -force "$output_dir/placed.dcp"
route_design -directive $route_directive
write_checkpoint -force "$output_dir/routed.dcp"
report_route_status -file "$output_dir/route_status.rpt"
report_drc -file "$output_dir/drc.rpt"
report_timing_summary -file "$output_dir/timing_summary.rpt"

set unrouted [get_nets -quiet -filter {ROUTE_STATUS == UNROUTED}]
if {[llength $unrouted] != 0} {
    error "route completed with [llength $unrouted] unrouted nets"
}
puts "EMUFLOW_MAPPED_VIVADO status=pass part=$part cells=$expected_cells fixed_lut_anchors=$fixed_lut_anchors ff_loc_repairs=$ff_loc_repairs routed_dcp=$output_dir/routed.dcp"
