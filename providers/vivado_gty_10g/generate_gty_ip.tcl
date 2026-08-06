# SPDX-License-Identifier: CERN-OHL-S-2.0
#
# EmuFlow adaptation of Taxi's UltraScale+ 10G GTY generation recipe.
# Upstream: https://github.com/fpganinja/taxi
# Upstream file: src/eth/rtl/us/taxi_eth_phy_10g_us_gty_156.tcl
# Upstream revision: d5d38d824149b68f7c9e0c3df24f337df6bf23de
#
# This editable recipe creates one GTY channel+common IP and one channel-only
# IP. Vivado-generated products belong in the build directory and are not
# source-complete EmuFlow implementations.

set preset {GTY-10GBASE-R}
set freerun_freq {125}
set line_rate {10.3125}
set refclk_freq {156.25}
set user_data_width {32}
set int_data_width $user_data_width
set qpll_fracn [expr {int(fmod($line_rate*1000/2 / $refclk_freq, 1)*pow(2, 24))}]

set channel_ports [list]
set common_ports [list]
lappend channel_ports drpclk_in drpaddr_in drpdi_in drpen_in drpwe_in drpdo_out drprdy_out
lappend common_ports drpclk_common_in drpaddr_common_in drpdi_common_in drpen_common_in drpwe_common_in drpdo_common_out drprdy_common_out
lappend common_ports qpll0reset_in qpll1reset_in qpll0pd_in qpll1pd_in
lappend common_ports gtrefclk00_in qpll0lock_out qpll0outclk_out qpll0outrefclk_out
lappend common_ports gtrefclk01_in qpll1lock_out qpll1outclk_out qpll1outrefclk_out
if {[string first uplus [get_property FAMILY [get_property PART [current_project]]]] != -1} {
    lappend common_ports pcierateqpll0_in pcierateqpll1_in
} else {
    lappend common_ports qpllrsvd2_in qpllrsvd3_in
}
lappend channel_ports gttxreset_in txuserrdy_in txpmareset_in txpcsreset_in txprogdivreset_in
lappend channel_ports txresetdone_out txpmaresetdone_out txprgdivresetdone_out
lappend channel_ports gtrxreset_in rxuserrdy_in rxpmareset_in rxdfelpmreset_in eyescanreset_in
lappend channel_ports rxpcsreset_in rxprogdivreset_in rxresetdone_out rxpmaresetdone_out rxprgdivresetdone_out
lappend channel_ports txpd_in txpdelecidlemode_in rxpd_in
lappend channel_ports txsysclksel_in txpllclksel_in rxsysclksel_in rxpllclksel_in
lappend channel_ports txpolarity_in rxpolarity_in
lappend channel_ports txelecidle_in txinhibit_in txdiffctrl_in txmaincursor_in txprecursor_in txpostcursor_in
lappend channel_ports rxcdrlock_out rxcdrhold_in rxcdrovrden_in rxlpmen_in

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
dict set config RX_EQ_MODE {DFE}
dict set config SECONDARY_QPLL_ENABLE {false}
dict set config LOCATE_RESET_CONTROLLER {EXAMPLE_DESIGN}
dict set config LOCATE_TX_USER_CLOCKING {CORE}
dict set config LOCATE_RX_USER_CLOCKING {CORE}
dict set config LOCATE_USER_DATA_WIDTH_SIZING {CORE}
dict set config FREERUN_FREQUENCY $freerun_freq
dict set config DISABLE_LOC_XDC {1}
dict set config TX_DATA_ENCODING {64B66B_ASYNC}
dict set config TX_BUFFER_MODE {1}
dict set config TX_OUTCLK_SOURCE {TXPROGDIVCLK}
dict set config RX_DATA_DECODING {64B66B_ASYNC}
dict set config RX_BUFFER_MODE {1}
dict set config RX_OUTCLK_SOURCE {RXPROGDIVCLK}

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

dict set config ENABLE_OPTIONAL_PORTS [concat $common_ports $channel_ports]
dict set config LOCATE_COMMON {CORE}
emuflow_create_gtwizard_ip emuflow_gty_10g_full $preset $config

dict set config ENABLE_OPTIONAL_PORTS $channel_ports
dict set config LOCATE_COMMON {EXAMPLE_DESIGN}
emuflow_create_gtwizard_ip emuflow_gty_10g_channel $preset $config
