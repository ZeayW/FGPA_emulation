# Copyright (c) 2021 Alex Forencich
# Copyright 2026 EmuFlow contributors
# SPDX-License-Identifier: MIT
#
# EmuFlow adaptation of the verilog-ethernet VCU108 10G GTY recipe.
# Upstream: https://github.com/alexforencich/verilog-ethernet
# Upstream file: example/VCU108/fpga_10g/ip/eth_xcvr_gt.tcl
# Upstream revision: 77320a9471d19c7dd383914bc049e02d9f4f1ffb
#
# The 64-bit + 2-bit-header user boundary matches serial-phy-provider/v3.
# Vivado-generated products stay in the build directory and are proprietary
# AMD implementation artifacts; this editable recipe is not an open GTY PHY.

set preset {GTY-10GBASE-R}
set freerun_freq {156.25}
set line_rate {10.3125}
set refclk_freq {156.25}
set qpll_fracn [expr {int(fmod($line_rate*1000/2 / $refclk_freq, 1)*pow(2, 24))}]
set user_data_width {64}
set int_data_width {64}
set extra_ports [list]
set extra_pll_ports [list {qpll0lock_out}]

set config [dict create]
dict set config TX_LINE_RATE $line_rate
dict set config TX_REFCLK_FREQUENCY $refclk_freq
dict set config TX_QPLL_FRACN_NUMERATOR $qpll_fracn
dict set config TX_USER_DATA_WIDTH $user_data_width
dict set config TX_INT_DATA_WIDTH $int_data_width
dict set config RX_LINE_RATE $line_rate
dict set config RX_REFCLK_FREQUENCY $refclk_freq
dict set config RX_QPLL_FRACN_NUMERATOR $qpll_fracn
dict set config RX_USER_DATA_WIDTH $user_data_width
dict set config RX_INT_DATA_WIDTH $int_data_width
dict set config ENABLE_OPTIONAL_PORTS $extra_ports
dict set config LOCATE_COMMON {CORE}
dict set config LOCATE_RESET_CONTROLLER {CORE}
dict set config LOCATE_TX_USER_CLOCKING {CORE}
dict set config LOCATE_RX_USER_CLOCKING {CORE}
dict set config LOCATE_USER_DATA_WIDTH_SIZING {CORE}
dict set config FREERUN_FREQUENCY $freerun_freq
dict set config DISABLE_LOC_XDC {1}

proc emuflow_create_gtwizard_ip {name preset config} {
    create_ip -name gtwizard_ultrascale -vendor xilinx.com -library ip -module_name $name
    set ip [get_ips $name]
    set_property CONFIG.preset $preset $ip
    set config_list {}
    dict for {property value} $config {
        lappend config_list "CONFIG.${property}" $value
    }
    set_property -dict $config_list $ip
}

# One instance per active quad owns the GTYE4_COMMON and its first channel.
dict set config ENABLE_OPTIONAL_PORTS [concat $extra_pll_ports $extra_ports]
dict set config LOCATE_COMMON {CORE}
emuflow_create_gtwizard_ip emuflow_gty_10g_full $preset $config

# The other active channels consume the full instance's shared QPLL outputs.
dict set config ENABLE_OPTIONAL_PORTS $extra_ports
dict set config LOCATE_COMMON {EXAMPLE_DESIGN}
emuflow_create_gtwizard_ip emuflow_gty_10g_channel $preset $config
