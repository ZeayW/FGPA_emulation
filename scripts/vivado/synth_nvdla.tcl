# SPDX-License-Identifier: Apache-2.0
#
# FPGA synthesis for the pinned NVDLA nvdlav1 RTL.  FPGA-safe SRAM wrapper
# declarations or functional models are supplied by the caller so proprietary
# ASIC MBIST/DFT cells never enter the implementation netlist.

if {$argc < 4 || $argc > 6} {
  puts stderr "usage: vivado -mode batch -source synth_nvdla.tcl -tclargs SOURCE_ROOT RAM_STUBS OUTPUT_DIR PART ?TOP? ?GATED_CLOCK_CONVERSION?"
  exit 2
}

set source_root [file normalize [lindex $argv 0]]
set ram_stubs [file normalize [lindex $argv 1]]
set output_dir [file normalize [lindex $argv 2]]
set part [lindex $argv 3]
set top NV_nvdla
if {$argc == 5} {
  set top [lindex $argv 4]
}
set gated_clock_conversion off
if {$argc == 6} {
  set top [lindex $argv 4]
  set gated_clock_conversion [lindex $argv 5]
}
if {$gated_clock_conversion ni {off on auto}} {
  puts stderr "GATED_CLOCK_CONVERSION must be off, on, or auto"
  exit 2
}
set output_stem nvdla
if {$top ne "NV_nvdla"} {
  set output_stem [string tolower $top]
}

if {![file isfile $ram_stubs]} {
  puts stderr "missing generated NVDLA RAM wrappers: $ram_stubs"
  exit 2
}
file mkdir $output_dir

set rtl_sources [lsort [glob -nocomplain \
  -directory [file join $source_root vmod nvdla] \
  -types f */*.v]]
set library_sources [lsort [glob -nocomplain \
  -directory [file join $source_root vmod vlibs] \
  -types f *.v]]

if {[llength $rtl_sources] < 250} {
  puts stderr "incomplete NVDLA source tree: found only [llength $rtl_sources] RTL files"
  exit 2
}

# nvdlav1's generated partition_o contains C-preprocessor-style feature
# guards although the rest of the release uses Verilog guards.  Normalize
# only those directive spellings into a generated file; no RTL statements
# are changed.
set partition_o [file join $source_root vmod nvdla top NV_NVDLA_partition_o.v]
set normalized_partition_o [file join $output_dir NV_NVDLA_partition_o.v]
set input [open $partition_o r]
set contents [read $input]
close $input
set contents [regsub -all -line {^#(ifdef|ifndef|else|endif)} \
  $contents {`\1}]
set output [open $normalized_partition_o w]
puts -nonewline $output $contents
close $output
set partition_index [lsearch -exact $rtl_sources $partition_o]
if {$partition_index < 0} {
  puts stderr "NVDLA partition_o was not found in the source manifest"
  exit 2
}
set rtl_sources [lreplace $rtl_sources $partition_index $partition_index \
  $normalized_partition_o]

# The ASIC clock-gate model samples its enable on the falling edge and gates
# the following high phase.  Vivado can preserve this behavior with register
# clock-enables, but only when synthesis is enabled for gated-clock conversion
# and the clock input inside the gate is identified explicitly.  Generate a
# tagged source copy so the pinned upstream checkout remains unmodified.
if {$gated_clock_conversion ne "off"} {
  set clock_gate [file join $source_root vmod vlibs CKLNQD12.v]
  set tagged_clock_gate [file join $output_dir CKLNQD12.v]
  set input [open $clock_gate r]
  set contents [read $input]
  close $input
  set replacements [regsub -all -line \
    {^[[:space:]]*input[[:space:]]+CP[[:space:]]*;} \
    $contents {(* gated_clock = "true" *) input CP;} contents]
  if {$replacements != 1} {
    puts stderr "expected one CP input in $clock_gate; found $replacements"
    exit 2
  }
  set output [open $tagged_clock_gate w]
  puts -nonewline $output $contents
  close $output
  set clock_gate_index [lsearch -exact $library_sources $clock_gate]
  if {$clock_gate_index < 0} {
    puts stderr "NVDLA CKLNQD12 clock gate was not found in the library manifest"
    exit 2
  }
  set library_sources [lreplace $library_sources \
    $clock_gate_index $clock_gate_index $tagged_clock_gate]
}

create_project -in_memory -part $part nvdla_screen
add_files -norecurse $ram_stubs
add_files -norecurse $library_sources
add_files -norecurse $rtl_sources
set_property file_type SystemVerilog [get_files]
set_property include_dirs [list [file join $source_root vmod include]] \
  [current_fileset]
set_property verilog_define [list \
  SYNTHESIS \
  DESIGNWARE_NOEXIST \
  NVDLA_BDMA_ENABLE \
  NVDLA_CDP_ENABLE \
  NVDLA_PDP_ENABLE \
  NVDLA_RUBIK_ENABLE \
] [current_fileset]
set_property top $top [current_fileset]

synth_design \
  -top $top \
  -part $part \
  -mode out_of_context \
  -flatten_hierarchy rebuilt \
  -gated_clock_conversion $gated_clock_conversion \
  -directive RuntimeOptimized

write_checkpoint -force [file join $output_dir ${output_stem}_synth.dcp]
write_edif -force [file join $output_dir ${output_stem}_synth.edf]
write_verilog -force -mode funcsim \
  [file join $output_dir ${output_stem}_synth.v]
report_utilization -hierarchical \
  -file [file join $output_dir utilization_hierarchical.rpt]

set cells [get_cells -hierarchical]
set primitives [get_cells -hierarchical -filter {IS_PRIMITIVE}]
set blackboxes [get_cells -hierarchical -filter {IS_BLACKBOX}]
set summary [open [file join $output_dir primitive_counts.tsv] w]
puts $summary "metric\tvalue"
puts $summary "all_cells\t[llength $cells]"
puts $summary "primitive_cells\t[llength $primitives]"
puts $summary "blackbox_cells\t[llength $blackboxes]"
array set counts {}
foreach cell $primitives {
  set reference [get_property REF_NAME $cell]
  if {[info exists counts($reference)]} {
    incr counts($reference)
  } else {
    set counts($reference) 1
  }
}
foreach reference [lsort [array names counts]] {
  puts $summary "$reference\t$counts($reference)"
}
close $summary

puts "EMUFLOW_NVDLA_RESULT gated_clock_conversion=$gated_clock_conversion [file join $output_dir primitive_counts.tsv]"
