#!/bin/sh
# /usr/bin/led-status-daemon.sh - Dynamic LED Status Monitor

set_led() {
    local r=$1 g=$2 b=$3 blink=$4
    if [ "$blink" = "1" ]; then
        echo none > /sys/class/leds/led_g/trigger 2>/dev/null; echo 0 > /sys/class/leds/led_g/brightness 2>/dev/null
        echo none > /sys/class/leds/led_b/trigger 2>/dev/null; echo 0 > /sys/class/leds/led_b/brightness 2>/dev/null
        echo timer > /sys/class/leds/led_r/trigger 2>/dev/null
        echo 300 > /sys/class/leds/led_r/delay_on 2>/dev/null
        echo 300 > /sys/class/leds/led_r/delay_off 2>/dev/null
    else
        echo none > /sys/class/leds/led_r/trigger 2>/dev/null
        echo none > /sys/class/leds/led_g/trigger 2>/dev/null
        echo none > /sys/class/leds/led_b/trigger 2>/dev/null
        echo $r > /sys/class/leds/led_r/brightness 2>/dev/null
        echo $g > /sys/class/leds/led_g/brightness 2>/dev/null
        echo $b > /sys/class/leds/led_b/brightness 2>/dev/null
    fi
}

current_state=""

while true; do
    # 1. Check if VPN is connected (Tailscale, WireGuard, OpenVPN/tun)
    vpn_active=0
    if ip link show tailscale0 2>/dev/null | grep -q "UP"; then
        vpn_active=1
    elif ip link show wg0 2>/dev/null | grep -q "UP"; then
        vpn_active=1
    elif ip link show tun0 2>/dev/null | grep -q "UP"; then
        vpn_active=1
    fi

    if [ "$vpn_active" = "1" ]; then
        if [ "$current_state" != "vpn" ]; then
            set_led 0 0 255 0
            current_state="vpn"
            logger -t led-daemon "State changed: VPN Connected (Blue)"
        fi
        sleep 3
        continue
    fi

    # 2. Check if Internet is connected via USB Tethering
    usb_tether=0
    def_iface=$(ip route show default 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1)}' | head -n 1)
    if [ "$def_iface" = "usb0" ] || [ "$def_iface" = "eth2" ]; then
        if ping -c 1 -W 2 1.1.1.1 >/dev/null 2>&1 || ping -c 1 -W 2 8.8.8.8 >/dev/null 2>&1; then
            usb_tether=1
        fi
    fi

    if [ "$usb_tether" = "1" ]; then
        if [ "$current_state" != "usb_tether" ]; then
            set_led 255 0 255 0
            current_state="usb_tether"
            logger -t led-daemon "State changed: USB Tethering Internet (Purple)"
        fi
        sleep 3
        continue
    fi

    # 3. Check general Internet connectivity (Ethernet/WAN)
    if ping -c 1 -W 2 1.1.1.1 >/dev/null 2>&1 || ping -c 1 -W 2 8.8.8.8 >/dev/null 2>&1 || ping -c 1 -W 2 192.168.1.1 >/dev/null 2>&1; then
        if [ "$current_state" != "online" ]; then
            set_led 0 255 0 0
            current_state="online"
            logger -t led-daemon "State changed: Internet Connected (Green)"
        fi
    else
        if [ "$current_state" != "offline" ]; then
            set_led 255 0 0 0
            current_state="offline"
            logger -t led-daemon "State changed: No Internet (Constant Red)"
        fi
    fi

    sleep 3
done
