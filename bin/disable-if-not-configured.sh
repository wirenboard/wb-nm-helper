#!/bin/bash

hostapd_interface_line=$(grep -E -o 'interface=.+$' /etc/hostapd.conf)
hostapd_interface=${hostapd_interface_line#interface=}

is_configured=$(awk -v hostapd_interface="$hostapd_interface" '/^iface/ && $2==hostapd_interface {print 0}' /etc/network/interfaces)

if [ -z "$is_configured" ]; then
    exit 1
else
    exit 0
fi
