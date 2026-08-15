# Export partition-independent OpenSTA paths with ordered EmuIR net identity.
#
# Required environment variables:
#   EMUFLOW_STA_LIBERTY
#   EMUFLOW_STA_VERILOG
#   EMUFLOW_STA_TOP
#   EMUFLOW_STA_NET_MAP
#   EMUFLOW_STA_CLOCKS
#   EMUFLOW_STA_OUTPUT
#   EMUFLOW_STA_MAX_PATHS
# Optional environment variable:
#   EMUFLOW_STA_THROUGH_NETS

proc emuflow_required_env {name} {
  global env
  if {![info exists env($name)] || $env($name) eq ""} {
    error "missing required environment variable $name"
  }
  return $env($name)
}

proc emuflow_hex_decode {value} {
  return [encoding convertfrom utf-8 [binary decode hex $value]]
}

proc emuflow_hex_encode {value} {
  return [binary encode hex [encoding convertto utf-8 $value]]
}

set liberty_path [file normalize [emuflow_required_env EMUFLOW_STA_LIBERTY]]
set verilog_path [file normalize [emuflow_required_env EMUFLOW_STA_VERILOG]]
set top [emuflow_required_env EMUFLOW_STA_TOP]
set map_path [file normalize [emuflow_required_env EMUFLOW_STA_NET_MAP]]
set clock_path [file normalize [emuflow_required_env EMUFLOW_STA_CLOCKS]]
set output_path [file normalize [emuflow_required_env EMUFLOW_STA_OUTPUT]]
set max_paths [emuflow_required_env EMUFLOW_STA_MAX_PATHS]
if {![string is integer -strict $max_paths] || $max_paths <= 0} {
  error "EMUFLOW_STA_MAX_PATHS must be a positive integer"
}

read_liberty $liberty_path
read_verilog $verilog_path
link_design $top

set clock_input [open $clock_path r]
set clock_lines [split [read $clock_input] "\n"]
close $clock_input
if {[lindex $clock_lines 0] ne "clock_hex\tperiod_ns"} {
  error "invalid OpenSTA clock-map header"
}
set clock_count 0
foreach line [lrange $clock_lines 1 end] {
  if {$line eq ""} {
    continue
  }
  set fields [split $line "\t"]
  if {[llength $fields] != 2} {
    error "malformed OpenSTA clock-map row"
  }
  set clock_name [emuflow_hex_decode [lindex $fields 0]]
  set period [lindex $fields 1]
  set port [get_ports -quiet [list $clock_name]]
  if {[llength $port] != 1} {
    error "clock port '$clock_name' is absent or ambiguous"
  }
  create_clock -name $clock_name -period $period $port
  incr clock_count
}
if {$clock_count == 0} {
  error "OpenSTA requires at least one clock"
}

set map_input [open $map_path r]
set map_lines [split [read $map_input] "\n"]
close $map_input
set map_header [lindex $map_lines 0]
if {$map_header ne "mapped_net_hex\temuir_net_hex" &&
    $map_header ne "vivado_net_hex\temuir_net_hex"} {
  error "invalid EmuIR net-map header"
}
array set emuir_by_mapped_net {}
foreach line [lrange $map_lines 1 end] {
  if {$line eq ""} {
    continue
  }
  set fields [split $line "\t"]
  if {[llength $fields] != 2} {
    error "malformed EmuIR net-map row"
  }
  set mapped_name [emuflow_hex_decode [lindex $fields 0]]
  set emuir_name [emuflow_hex_decode [lindex $fields 1]]
  set emuir_by_mapped_net($mapped_name) $emuir_name
}

set output [open $output_path w]
puts $output "path_id_hex\tclock_domain_hex\tclock_period_ns\tslack_ns\tfixed_delay_ns\tpath_nets_hex"
set emitted 0
set queried_paths 0

# Path handles returned by find_timing_paths are owned by OpenSTA and may be
# invalidated by a subsequent query.  Serialize each query immediately instead
# of retaining those handles across the per-cut-net loop.
proc emuflow_emit_timing_paths {timing_paths output_var emitted_var} {
  global emuir_by_mapped_net
  upvar 1 $output_var output
  upvar 1 $emitted_var emitted
  foreach path_end $timing_paths {
    set endpoint_clock [get_property $path_end endpoint_clock]
    if {$endpoint_clock eq "NULL"} {
      continue
    }
    set clock_name [get_property $endpoint_clock name]
    set clock_period [get_property $endpoint_clock period]
    set slack [get_property $path_end slack]
    set points [get_property $path_end points]
    if {[llength $points] == 0} {
      continue
    }
    set fixed_delay [get_property [lindex $points end] arrival]
    set startpoint [get_property $path_end startpoint]
    set endpoint [get_property $path_end endpoint]
    set start_name [get_property $startpoint full_name]
    set end_name [get_property $endpoint full_name]

    set path_nets [list]
    unset -nocomplain seen_net
    array set seen_net {}
    foreach point $points {
      set pin [get_property $point pin]
      foreach net [get_nets -quiet -of_objects $pin] {
        set mapped_name [get_property $net full_name]
        if {![info exists emuir_by_mapped_net($mapped_name)]} {
          set mapped_name [get_property $net name]
        }
        if {[info exists emuir_by_mapped_net($mapped_name)]} {
          set emuir_name $emuir_by_mapped_net($mapped_name)
          if {![info exists seen_net($emuir_name)]} {
            set seen_net($emuir_name) 1
            lappend path_nets $emuir_name
          }
        }
      }
    }
    if {[llength $path_nets] == 0} {
      continue
    }
    set path_hex [list]
    foreach net $path_nets {
      lappend path_hex [emuflow_hex_encode $net]
    }
    set path_id "$start_name->$end_name#[format %08d $emitted]"
    puts $output "[emuflow_hex_encode $path_id]\t[emuflow_hex_encode $clock_name]\t$clock_period\t$slack\t$fixed_delay\t[join $path_hex ,]"
    incr emitted
  }
}

if {[info exists env(EMUFLOW_STA_THROUGH_NETS)] &&
    $env(EMUFLOW_STA_THROUGH_NETS) ne ""} {
  set through_path [file normalize $env(EMUFLOW_STA_THROUGH_NETS)]
  set through_input [open $through_path r]
  set through_lines [split [read $through_input] "\n"]
  close $through_input
  if {[lindex $through_lines 0] ne "mapped_net_hex\temuir_net_hex"} {
    error "invalid OpenSTA through-net map header"
  }
  foreach line [lrange $through_lines 1 end] {
    if {$line eq ""} {
      continue
    }
    set fields [split $line "\t"]
    if {[llength $fields] != 2} {
      error "malformed OpenSTA through-net map row"
    }
    set mapped_name [emuflow_hex_decode [lindex $fields 0]]
    set through_net [get_nets -quiet [list $mapped_name]]
    if {[llength $through_net] != 1} {
      error "through net '$mapped_name' is absent or ambiguous"
    }
    # OpenSTA 2.6 accepts pins and nets for -through, but its Tcl net
    # collection path can dereference invalid state.  Resolve the net to its
    # connected pins first; this is semantically equivalent for a timing path.
    set through_pins [get_pins -quiet -of_objects $through_net]
    if {[llength $through_pins] == 0} {
      error "through net '$mapped_name' has no timing pins"
    }
    # OpenSTA does not treat an internal combinational driver as a legal timing
    # startpoint, so querying -from the cut-net driver silently returns no path.
    # Its 2.6 -through collection path is also unsafe.  Instead, independently
    # reconstruct the cut's timing cone and query from its real sequential/input
    # startpoints to its real sequential/output endpoints.  The serialized path
    # is still checked below (and again by Python) for the requested EmuIR net,
    # so a reconvergent bypass cannot satisfy the coverage certificate.
    set driver_count 0
    foreach through_pin $through_pins {
      if {[get_property $through_pin direction] ne "output"} {
        continue
      }
      incr driver_count
      set startpoints [get_fanin -flat -startpoints_only \
        -to [list $through_pin]]
      set endpoints [get_fanout -flat -endpoints_only \
        -from [list $through_pin]]
      if {[llength $startpoints] == 0} {
        error "through net '$mapped_name' has no timing startpoints"
      }
      if {[llength $endpoints] == 0} {
        error "through net '$mapped_name' has no timing endpoints"
      }
      foreach path_end [find_timing_paths -path_delay max \
          -from $startpoints -to $endpoints \
          -group_count 1 -endpoint_count 1 \
          -sort_by_slack] {
        set timing_paths [list $path_end]
        incr queried_paths
        emuflow_emit_timing_paths $timing_paths output emitted
      }
    }
    if {$driver_count == 0} {
      error "through net '$mapped_name' has no driver pin"
    }
  }
} else {
  set timing_paths [find_timing_paths -path_delay max \
    -group_count $max_paths -endpoint_count 1 -sort_by_slack]
  set queried_paths [llength $timing_paths]
  emuflow_emit_timing_paths $timing_paths output emitted
}
close $output

if {$emitted == 0} {
  error "OpenSTA found no timing paths containing mapped EmuIR nets"
}
puts "EMUFLOW_OPENSTA_DATABASE status=pass clocks=$clock_count queried_paths=$queried_paths emitted_paths=$emitted output=$output_path"
