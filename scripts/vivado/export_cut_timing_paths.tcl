# Export STA paths that traverse EmuFlow cut nets.
#
# The map and result encode all names as UTF-8 hex so tabs, spaces, escaped
# Verilog identifiers, and brackets remain lossless.

if {$argc != 4} {
  error "usage: vivado -mode batch -source export_cut_timing_paths.tcl -tclargs INPUT_DCP CUT_NET_MAP_TSV OUTPUT_TSV MAX_PATHS"
}
set input_dcp [file normalize [lindex $argv 0]]
set map_path [file normalize [lindex $argv 1]]
set output_path [file normalize [lindex $argv 2]]
set max_paths [lindex $argv 3]
if {![string is integer -strict $max_paths] || $max_paths <= 0} {
  error "MAX_PATHS must be a positive integer"
}

proc emuflow_hex_decode {value} {
  return [encoding convertfrom utf-8 [binary decode hex $value]]
}
proc emuflow_hex_encode {value} {
  return [binary encode hex [encoding convertto utf-8 $value]]
}

open_checkpoint $input_dcp
set input [open $map_path r]
set lines [split [read $input] "\n"]
close $input
if {[lindex $lines 0] ne "vivado_net_hex\tcut_net_hex"} {
  error "invalid cut-net map header"
}

array set cut_by_vivado_net {}
array set object_by_vivado_net {}
foreach object [get_nets -quiet -hier -filter {NAME =~ *__emuflow_net_*}] {
  set object_by_vivado_net([get_property NAME $object]) $object
}
set mapped_objects [list]
foreach line [lrange $lines 1 end] {
  if {$line eq ""} {
    continue
  }
  set fields [split $line "\t"]
  if {[llength $fields] != 2} {
    error "malformed cut-net map row"
  }
  set vivado_name [emuflow_hex_decode [lindex $fields 0]]
  set cut_name [emuflow_hex_decode [lindex $fields 1]]
  if {![info exists object_by_vivado_net($vivado_name)]} {
    continue
  }
  set object $object_by_vivado_net($vivado_name)
  set cut_by_vivado_net($vivado_name) $cut_name
  lappend mapped_objects $object
}
if {[llength $mapped_objects] == 0} {
  error "none of the mapped EmuFlow cut nets exist in the checkpoint"
}

set timing_paths [get_timing_paths -quiet -setup -nworst 1 \
  -max_paths $max_paths -through $mapped_objects]
set output [open $output_path w]
puts $output "path_id_hex\tclock_domain_hex\tclock_period_ns\tslack_ns\tfixed_delay_ns\tcut_nets_hex"
set emitted 0
foreach path $timing_paths {
  set cut_names [list]
  unset -nocomplain seen_cut
  array set seen_cut {}
  foreach net [get_nets -quiet -of_objects $path] {
    set name [get_property NAME $net]
    if {[info exists cut_by_vivado_net($name)] &&
        ![info exists seen_cut($cut_by_vivado_net($name))]} {
      set cut_name $cut_by_vivado_net($name)
      set seen_cut($cut_name) 1
      lappend cut_names $cut_name
    }
  }
  if {[llength $cut_names] == 0} {
    continue
  }
  set cut_hex [list]
  foreach cut_name $cut_names {
    lappend cut_hex [emuflow_hex_encode $cut_name]
  }
  set path_id "vivado_path_[format %08d $emitted]"
  set group [get_property GROUP $path]
  set period [get_property REQUIREMENT $path]
  set slack [get_property SLACK $path]
  set fixed_delay [get_property DATAPATH_DELAY $path]
  puts $output "[emuflow_hex_encode $path_id]\t[emuflow_hex_encode $group]\t$period\t$slack\t$fixed_delay\t[join $cut_hex ,]"
  incr emitted
}
close $output
puts "EMUFLOW_CUT_STA status=pass mapped_cut_nets=[llength $mapped_objects] queried_paths=[llength $timing_paths] emitted_paths=$emitted output=$output_path"
