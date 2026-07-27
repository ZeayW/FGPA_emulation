if {$argc < 2} {
    error "usage: vivado -mode batch -source export_architecture.tcl -tclargs PART OUTPUT_TSV ?SITE_LIMIT?"
}

set part [lindex $argv 0]
set output_path [file normalize [lindex $argv 1]]
set site_limit 0
if {$argc >= 3} {
    set site_limit [lindex $argv 2]
}
set site_side 0
if {$site_limit > 0} {
    while {$site_side * $site_side < $site_limit} {
        incr site_side
    }
}

create_project -in_memory -part $part
# Device queries require an open design in Vivado. Synthesize the small,
# primitive-only regression design to establish the target device context.
set script_dir [file dirname [file normalize [info script]]]
set probe_rtl [file normalize "$script_dir/../../examples/rtl/phase2_primitives.v"]
read_verilog $probe_rtl
synth_design -top phase2_primitives -part $part -flatten_hierarchy none
set output [open $output_path w]
puts $output "META\tpart\t$part"
puts $output "META\tvivado_version\t[version -short]"
puts $output "# Phase 2 inventory: 6LUT plus primary and FF2 register BELs."

set emitted 0
foreach site [lsort -dictionary [get_sites -filter {SITE_TYPE =~ SLICE*}]] {
    if {![regexp {^SLICE_X([0-9]+)Y([0-9]+)$} $site match x y]} {
        continue
    }
    # A single-column device sample is numerically degenerate for the
    # two-dimensional electrostatic placer. Keep a compact square window.
    if {$site_limit > 0 && ($x >= $site_side || $y >= $site_side)} {
        continue
    }
    if {$site_limit > 0 && $emitted >= $site_limit} {
        break
    }
    set site_type [get_property SITE_TYPE $site]
    puts $output "SITE\t$site\t$site_type\t$x\t$y"
    set z 0
    foreach letter {A B C D E F G H} {
        set bel_name "${letter}6LUT"
        set bel [get_bels -quiet "$site/$bel_name"]
        if {[llength $bel] == 1} {
            puts $output "BEL\t$bel_name\tLUT6\t$z"
        }
        incr z
    }
    set z 0
    foreach letter {A B C D E F G H} {
        set bel_name "${letter}FF"
        set bel [get_bels -quiet "$site/$bel_name"]
        if {[llength $bel] == 1} {
            puts $output "BEL\t$bel_name\tFF\t$z"
        }
        incr z
    }
    set z 8
    foreach letter {A B C D E F G H} {
        set bel_name "${letter}FF2"
        set bel [get_bels -quiet "$site/$bel_name"]
        if {[llength $bel] == 1} {
            puts $output "BEL\t$bel_name\tFF\t$z"
        }
        incr z
    }
    incr emitted
}
close $output

if {$emitted == 0} {
    error "no SLICE sites were exported for $part"
}
puts "EMUFLOW_ARCH_EXPORT part=$part sites=$emitted output=$output_path"
