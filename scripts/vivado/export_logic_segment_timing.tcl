# Export routed endpoint-to-endpoint DUT logic delays for Phase 7C.

if {$argc != 3} {
  error "usage: export_logic_segment_timing.tcl INPUT_DCP QUERY_TSV OUTPUT_TSV"
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
proc emuflow_resolve_object {kind name} {
  if {$kind eq "pin"} {
    set object [get_pins -quiet -hier [list $name]]
    # Vivado does not consistently resolve a leaf pin by its full hierarchical
    # name after a routed checkpoint is reopened, even when the parent cell and
    # pin are both present. Resolve the exact parent cell first as a stable
    # fallback; this is required for inferred RAMB clock endpoints.
    if {[llength $object] == 0} {
      set separator [string last "/" $name]
      if {$separator > 0} {
        set cell_name [string range $name 0 [expr {$separator - 1}]]
        set pin_name [string range $name [expr {$separator + 1}] end]
        set cell [get_cells -quiet -hier -filter "NAME == $cell_name"]
        if {[llength $cell] == 1} {
          set candidates [list]
          foreach candidate [get_pins -quiet -of_objects $cell] {
            set candidate_name [get_property NAME $candidate]
            set candidate_separator [string last "/" $candidate_name]
            set candidate_leaf [string range $candidate_name \
              [expr {$candidate_separator + 1}] end]
            if {$candidate_leaf eq $pin_name} {
              lappend candidates $candidate
            }
          }
          set object $candidates
        }
      }
    }
  } elseif {$kind eq "port"} {
    set object [get_ports -quiet [list $name]]
  } else {
    error "invalid timing object kind $kind"
  }
  if {[llength $object] != 1} {
    error "timing object $kind $name resolves to [llength $object] objects"
  }
  return $object
}

open_checkpoint $input_dcp
set input [open $query_path r]
set lines [split [read $input] "\n"]
close $input
if {[lindex $lines 0] ne "endpoint_hex\tkind\tstart_kind\tstart_object_hex\tend_kind\tend_object_hex"} {
  error "invalid logic segment timing query header"
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
    error "malformed logic segment timing query row"
  }
  set endpoint_hex [lindex $fields 0]
  set kind [lindex $fields 1]
  set start_kind [lindex $fields 2]
  set start_name [emuflow_hex_decode [lindex $fields 3]]
  set end_kind [lindex $fields 4]
  set end_name [emuflow_hex_decode [lindex $fields 5]]
  set start_object [emuflow_resolve_object $start_kind $start_name]
  set end_object [emuflow_resolve_object $end_kind $end_name]
  set paths [get_timing_paths -quiet -max_paths 1 -nworst 1 \
    -from $start_object -to $end_object]
  if {[llength $paths] != 1} {
    error "logic segment [emuflow_hex_decode $endpoint_hex] has no routed timing path"
  }
  set delay [get_property DATAPATH_DELAY [lindex $paths 0]]
  if {$delay eq ""} {
    error "logic segment [emuflow_hex_decode $endpoint_hex] has no datapath delay"
  }
  puts $output "$endpoint_hex\t$kind\t$delay\t[emuflow_hex_encode $start_name]\t[emuflow_hex_encode $end_name]"
  incr emitted
}
close $output
puts "EMUFLOW_LOGIC_SEGMENT_TIMING status=pass segments=$emitted output=$output_path"
