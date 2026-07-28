if {$argc != 3 && $argc != 4} {
    error "usage: vivado -mode batch -source report_runtime_contract.tcl -tclargs ROUTED_DCP OUTPUT_DIR EXPECTED_CELLS ?MAPPED_CELL_INVENTORY?"
}

set checkpoint [file normalize [lindex $argv 0]]
set output_dir [file normalize [lindex $argv 1]]
set expected_cells [lindex $argv 2]
file mkdir $output_dir
open_checkpoint $checkpoint

set cells [get_cells -hier -filter {REF_NAME != GND && REF_NAME != VCC}]
set physical_cells [llength $cells]
set infrastructure_cells 0
if {$argc == 3} {
    if {$physical_cells != $expected_cells} {
        error "routed checkpoint has $physical_cells cells; expected $expected_cells"
    }
} else {
    # Placement may legally insert physical clock infrastructure (for
    # example, a BUFG for a high-fanout converted clock-enable net). Audit
    # every pre-placement mapped identity and allow only explicitly
    # whitelisted infrastructure additions. This distinguishes a legal
    # physical increment from dropped, renamed, or arbitrary extra logic.
    set inventory_path [file normalize [lindex $argv 3]]
    if {![file isfile $inventory_path]} {
        error "mapped-cell inventory does not exist: $inventory_path"
    }
    set cell_names [get_property NAME $cells]
    set cell_refs [get_property REF_NAME $cells]
    array set routed_ref_by_name {}
    foreach name $cell_names ref_name $cell_refs {
        if {[info exists routed_ref_by_name($name)]} {
            error "routed checkpoint contains duplicate cell name $name"
        }
        set routed_ref_by_name($name) $ref_name
    }
    set inventory_file [open $inventory_path r]
    set inventory_lines [split [read $inventory_file] "\n"]
    close $inventory_file
    set mapped_inventory_cells 0
    foreach name $inventory_lines {
        if {$name eq "" || $name eq "GND" || $name eq "VCC"} {
            continue
        }
        incr mapped_inventory_cells
        if {![info exists routed_ref_by_name($name)]} {
            error "mapped cell $name is missing from routed checkpoint"
        }
        unset routed_ref_by_name($name)
    }
    if {$mapped_inventory_cells != $expected_cells} {
        error "mapped-cell inventory has $mapped_inventory_cells cells; expected $expected_cells"
    }
    set infrastructure_inventory [open "$output_dir/infrastructure_cells.tsv" w]
    puts $infrastructure_inventory "name\tref_name"
    foreach name [lsort -ascii [array names routed_ref_by_name]] {
        set ref_name $routed_ref_by_name($name)
        if {![string match "BUFG*" $ref_name]} {
            error "unapproved physical infrastructure cell $name has type $ref_name"
        }
        puts $infrastructure_inventory "$name\t$ref_name"
        incr infrastructure_cells
    }
    close $infrastructure_inventory
    if {$physical_cells != $expected_cells + $infrastructure_cells} {
        error "physical/mapped/infrastructure cell accounting is inconsistent"
    }
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
proc emuflow_clock_pair_timing {from_clock to_clock label} {
    set paths [get_timing_paths -quiet -max_paths 1 -nworst 1 \
        -from $from_clock -to $to_clock]
    if {[llength $paths] == 0} {
        # A tiny partition may contain only one DUT register and therefore no
        # same-domain launch/capture pair. With no path there is no possible
        # violation; record a conservative zero slack and explicit absence
        # instead of inventing positive timing margin.
        puts "EMUFLOW_RUNTIME_TIMING_EMPTY label=$label"
        return [list 0.0 0]
    }
    if {[llength $paths] != 1} {
        error "$label produced an unexpected timing-path count"
    }
    set slack [get_property SLACK $paths]
    if {$slack < 0.0} {
        error "$label timing failed with WNS $slack ns"
    }
    return [list $slack 1]
}
set dut_timing [emuflow_clock_pair_timing \
    $dut_clocks $dut_clocks dut]
set fabric_timing [emuflow_clock_pair_timing \
    $fabric_clocks $fabric_clocks fabric]
set fabric_to_dut_timing [emuflow_clock_pair_timing \
    $fabric_clocks $dut_clocks fabric_to_dut]
set dut_wns [lindex $dut_timing 0]
set dut_path_present [lindex $dut_timing 1]
set fabric_wns [lindex $fabric_timing 0]
set fabric_path_present [lindex $fabric_timing 1]
set fabric_to_dut_wns [lindex $fabric_to_dut_timing 0]
set fabric_to_dut_path_present [lindex $fabric_to_dut_timing 1]

set metrics [open "$output_dir/runtime_metrics.tsv" w]
puts $metrics "metric\tvalue"
puts $metrics "cells\t$expected_cells"
puts $metrics "mapped_cells\t$expected_cells"
puts $metrics "physical_cells\t$physical_cells"
puts $metrics "infrastructure_cells\t$infrastructure_cells"
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
puts $metrics "dut_path_present\t$dut_path_present"
puts $metrics "fabric_path_present\t$fabric_path_present"
puts $metrics "fabric_to_dut_path_present\t$fabric_to_dut_path_present"
close $metrics

puts "EMUFLOW_RUNTIME_VIVADO status=pass mapped_cells=$expected_cells physical_cells=$physical_cells infrastructure_cells=$infrastructure_cells unrouted_nets=0 drc_violations=0 dut_period_ns=$dut_period fabric_period_ns=$fabric_period wns_ns=$wns dut_wns_ns=$dut_wns dut_path_present=$dut_path_present fabric_wns_ns=$fabric_wns fabric_path_present=$fabric_path_present fabric_to_dut_wns_ns=$fabric_to_dut_wns fabric_to_dut_path_present=$fabric_to_dut_path_present"
