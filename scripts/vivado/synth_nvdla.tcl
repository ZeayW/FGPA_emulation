# SPDX-License-Identifier: Apache-2.0
#
# FPGA scale screen for the pinned NVDLA nvdlav1 RTL.  SRAM wrapper modules
# are supplied as black boxes by the caller; this keeps the experiment focused
# on the connected accelerator logic instead of expanding proprietary ASIC
# MBIST/DFT cells into LUTs.

if {$argc != 4} {
  puts stderr "usage: vivado -mode batch -source synth_nvdla.tcl -tclargs SOURCE_ROOT RAM_STUBS OUTPUT_DIR PART"
  exit 2
}

set source_root [file normalize [lindex $argv 0]]
set ram_stubs [file normalize [lindex $argv 1]]
set output_dir [file normalize [lindex $argv 2]]
set part [lindex $argv 3]

if {![file isfile $ram_stubs]} {
  puts stderr "missing generated NVDLA RAM black boxes: $ram_stubs"
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
set_property top NV_nvdla [current_fileset]

synth_design \
  -top NV_nvdla \
  -part $part \
  -mode out_of_context \
  -flatten_hierarchy rebuilt \
  -directive RuntimeOptimized

write_checkpoint -force [file join $output_dir nvdla_synth.dcp]
write_edif -force [file join $output_dir nvdla_synth.edf]
write_verilog -force -mode funcsim [file join $output_dir nvdla_synth.v]
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

puts "EMUFLOW_NVDLA_RESULT [file join $output_dir primitive_counts.tsv]"
