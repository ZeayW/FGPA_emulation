# SPDX-License-Identifier: Apache-2.0
#
# Run a reproducible, out-of-context synthesis screen of the upstream
# VeeR EH1 veer_wrapper.  The source tree and generated configuration are
# supplied by the caller so the benchmark revision remains external to this
# repository.

if {$argc != 4} {
  puts stderr "usage: vivado -mode batch -source synth_veer_eh1.tcl -tclargs SOURCE_ROOT CONFIG_DIR OUTPUT_DIR PART"
  exit 2
}

set source_root [file normalize [lindex $argv 0]]
set config_dir [file normalize [lindex $argv 1]]
set output_dir [file normalize [lindex $argv 2]]
set part [lindex $argv 3]

file mkdir $output_dir

set relative_sources [list \
  design/include/veer_types.sv \
  design/lib/beh_lib.sv \
  design/mem.sv \
  design/pic_ctrl.sv \
  design/dma_ctrl.sv \
  design/ifu/ifu_aln_ctl.sv \
  design/ifu/ifu_compress_ctl.sv \
  design/ifu/ifu_ifc_ctl.sv \
  design/ifu/ifu_bp_ctl.sv \
  design/ifu/ifu_ic_mem.sv \
  design/ifu/ifu_mem_ctl.sv \
  design/ifu/ifu_iccm_mem.sv \
  design/ifu/ifu.sv \
  design/dec/dec_decode_ctl.sv \
  design/dec/dec_gpr_ctl.sv \
  design/dec/dec_ib_ctl.sv \
  design/dec/dec_tlu_ctl.sv \
  design/dec/dec_trigger.sv \
  design/dec/dec.sv \
  design/exu/exu_alu_ctl.sv \
  design/exu/exu_mul_ctl.sv \
  design/exu/exu_div_ctl.sv \
  design/exu/exu.sv \
  design/lsu/lsu.sv \
  design/lsu/lsu_bus_buffer.sv \
  design/lsu/lsu_clkdomain.sv \
  design/lsu/lsu_addrcheck.sv \
  design/lsu/lsu_lsc_ctl.sv \
  design/lsu/lsu_stbuf.sv \
  design/lsu/lsu_bus_intf.sv \
  design/lsu/lsu_ecc.sv \
  design/lsu/lsu_dccm_mem.sv \
  design/lsu/lsu_dccm_ctl.sv \
  design/lsu/lsu_trigger.sv \
  design/dbg/dbg.sv \
  design/dmi/dmi_wrapper.v \
  design/dmi/dmi_jtag_to_core_sync.v \
  design/dmi/rvjtag_tap.sv \
  design/lib/mem_lib.sv \
  design/lib/ahb_to_axi4.sv \
  design/lib/axi4_to_ahb.sv \
  design/veer.sv \
  design/veer_wrapper.sv \
]

set common_defines [file join $config_dir common_defines.vh]
if {![file isfile $common_defines]} {
  puts stderr "missing generated configuration: $common_defines"
  exit 2
}

create_project -in_memory -part $part veer_eh1_screen
add_files -norecurse $common_defines
set_property is_global_include true [get_files $common_defines]

set sources [list]
foreach relative $relative_sources {
  set source [file join $source_root $relative]
  if {![file isfile $source]} {
    puts stderr "missing VeeR EH1 source: $source"
    exit 2
  }
  lappend sources $source
}
add_files -norecurse $sources
set_property file_type SystemVerilog [get_files $sources]
set_property include_dirs [list \
  [file join $source_root design include] \
  $config_dir \
] [current_fileset]
set_property top veer_wrapper [current_fileset]

synth_design \
  -top veer_wrapper \
  -part $part \
  -mode out_of_context \
  -flatten_hierarchy rebuilt \
  -directive RuntimeOptimized

write_checkpoint -force [file join $output_dir veer_eh1_synth.dcp]
write_edif -force [file join $output_dir veer_eh1_synth.edf]
write_verilog -force -mode funcsim [file join $output_dir veer_eh1_synth.v]
report_utilization -hierarchical -file [file join $output_dir utilization_hierarchical.rpt]
report_timing_summary -delay_type max -max_paths 10 \
  -file [file join $output_dir timing_summary.rpt]

set cells [get_cells -hierarchical]
set primitives [get_cells -hierarchical -filter {IS_PRIMITIVE}]
set summary [open [file join $output_dir primitive_counts.tsv] w]
puts $summary "metric\tvalue"
puts $summary "all_cells\t[llength $cells]"
puts $summary "primitive_cells\t[llength $primitives]"
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

puts "EMUFLOW_VEER_EH1_RESULT [file join $output_dir primitive_counts.tsv]"
