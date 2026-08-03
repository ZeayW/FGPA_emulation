# Export one routed timing measurement for every EmuFlow TDM endpoint.

if {$argc != 3} {
  error "usage: export_boundary_timing.tcl INPUT_DCP QUERY_TSV OUTPUT_TSV"
}
set input_dcp [file normalize [lindex $argv 0]]
set query_path [file normalize [lindex $argv 1]]
set output_path [file normalize [lindex $argv 2]]

proc emuflow_hex_decode {value} {
  return [encoding convertfrom utf-8 [binary decode hex $value]]
}
proc emuflow_hex_encode {value} {
  return [binary encode hex [encoding convertto utf-8 $value]]
}

open_checkpoint $input_dcp
set input [open $query_path r]
set lines [split [read $input] "\n"]
close $input
if {[lindex $lines 0] ne "endpoint_hex\tkind\texternal_port_hex\tbit\tlogical_net_hex\tboundary_cell_hex"} {
  error "invalid boundary timing query header"
}

set output [open $output_path w]
puts $output "endpoint_hex\tkind\tdelay_ns\tstart_object_hex\tend_object_hex"
set emitted 0
foreach line [lrange $lines 1 end] {
  if {$line eq ""} {
    continue
  }
  set fields [split $line "\t"]
  if {[llength $fields] != 6} {
    error "malformed boundary timing query row"
  }
  set endpoint_hex [lindex $fields 0]
  set kind [lindex $fields 1]
  set port [emuflow_hex_decode [lindex $fields 2]]
  set bit [lindex $fields 3]
  set logical_net [emuflow_hex_decode [lindex $fields 4]]
  set boundary_cell [emuflow_hex_decode [lindex $fields 5]]
  set port_bit [format {%s[%d]} $port $bit]
  set port_object [get_ports -quiet [list $port_bit]]
  if {[llength $port_object] != 1} {
    error "endpoint [emuflow_hex_decode $endpoint_hex] port $port_bit is absent"
  }

  if {$kind eq "tx"} {
    if {$boundary_cell ne ""} {
      set cell_object [get_cells -quiet -hier [list $boundary_cell]]
      set source_pin [get_pins -quiet -of_objects $cell_object \
        -filter {REF_PIN_NAME == Q}]
      if {[llength $source_pin] != 1} {
        error "TX endpoint [emuflow_hex_decode $endpoint_hex] has no unique shadow Q pin"
      }
      set paths [get_timing_paths -quiet -max_paths 1 -nworst 1 \
        -from $source_pin -to $port_object]
      set start_object [get_property NAME $source_pin]
    } else {
      set net_object [get_nets -quiet -hier [list $logical_net]]
      # Synthesis can legally merge or rename a DUT net while preserving the
      # dedicated EmuFlow TX output bit.  Recover the actual routed net from
      # that stable port instead of depending on the pre-synthesis net name.
      if {[llength $net_object] != 1} {
        set net_object [get_nets -quiet -of_objects $port_object]
      }
      if {[llength $net_object] != 1} {
        error "TX endpoint [emuflow_hex_decode $endpoint_hex] has no unique routed port net"
      }
      set source_pin [get_pins -quiet -leaf -of_objects $net_object \
        -filter {DIRECTION == OUT}]
      set source_port [get_ports -quiet -of_objects $net_object \
        -filter {DIRECTION == IN}]
      set source_object [concat $source_pin $source_port]
      if {[llength $source_object] != 1} {
        error "TX endpoint [emuflow_hex_decode $endpoint_hex] has no unique source object"
      }
      set paths [get_timing_paths -quiet -max_paths 1 -nworst 1 \
        -from $source_object -to $port_object]
      set start_object [get_property NAME $source_object]
      # A combinational leaf output is not a legal Vivado timing startpoint.
      # Constrain the routed path through that unique driver in this case and
      # report the actual upstream sequential/input startpoint.
      if {[llength $paths] == 0 && [llength $source_pin] == 1} {
        set paths [get_timing_paths -quiet -max_paths 1 -nworst 1 \
          -through $source_pin -to $port_object]
        if {[llength $paths] == 1} {
          set start_object [get_property STARTPOINT_PIN [lindex $paths 0]]
        }
      }
    }
    set end_object $port_bit
  } elseif {$kind eq "rx"} {
    set cell_object [get_cells -quiet -hier [list $boundary_cell]]
    if {[llength $cell_object] != 1} {
      error "RX endpoint [emuflow_hex_decode $endpoint_hex] boundary cell is absent"
    }
    set data_pin [get_pins -quiet -of_objects $cell_object \
      -filter {REF_PIN_NAME == D}]
    if {[llength $data_pin] != 1} {
      error "RX endpoint [emuflow_hex_decode $endpoint_hex] has no unique D pin"
    }
    set paths [get_timing_paths -quiet -max_paths 1 -nworst 1 \
      -from $port_object -to $data_pin]
    set start_object $port_bit
    set end_object [get_property NAME $data_pin]
  } else {
    error "invalid boundary endpoint kind $kind"
  }
  if {[llength $paths] != 1} {
    error "endpoint [emuflow_hex_decode $endpoint_hex] has no routed timing path"
  }
  set delay [get_property DATAPATH_DELAY [lindex $paths 0]]
  if {$delay eq ""} {
    error "endpoint [emuflow_hex_decode $endpoint_hex] has no datapath delay"
  }
  puts $output "$endpoint_hex\t$kind\t$delay\t[emuflow_hex_encode $start_object]\t[emuflow_hex_encode $end_object]"
  incr emitted
}
close $output
puts "EMUFLOW_BOUNDARY_TIMING status=pass endpoints=$emitted output=$output_path"
