#!/bin/sh
ROOT="/mnt"

# Prevent duplicate execution if already running on port 8888
if netstat -tlpn 2>/dev/null | grep -q ":8888 "; then
    exit 0
fi

# Ensure USB Tethering kernel modules are loaded
for mod in mii usbnet cdc_ether rndis_host ipheth; do
    grep -q "^$mod " /proc/modules || insmod /proc/1/root/lib/modules/5.4.225/$mod.ko 2>/dev/null || true
done

mkdir -p $ROOT/proc $ROOT/sys $ROOT/dev $ROOT/dev/pts $ROOT/tmp $ROOT/tmp/lock $ROOT/tmp/run
grep -q "$ROOT/proc" /proc/mounts || mount -t proc /proc $ROOT/proc
grep -q "$ROOT/sys" /proc/mounts || mount -t sysfs /sys $ROOT/sys
grep -q "$ROOT/dev " /proc/mounts || mount --bind /dev $ROOT/dev
grep -q "$ROOT/dev/pts" /proc/mounts || mount -t devpts /dev/pts $ROOT/dev/pts
grep -q "$ROOT/tmp" /proc/mounts || mount -t tmpfs -o size=32M tmpfs $ROOT/tmp

mkdir -p $ROOT/tmp/run/ubus
grep -q "$ROOT/tmp/run/ubus" /proc/mounts || mount --bind /var/run/ubus $ROOT/tmp/run/ubus

# Ensure active router configurations and ACLs are present
cp -f /etc/config/wireless $ROOT/etc/config/wireless 2>/dev/null || true
cp -f /etc/config/network $ROOT/etc/config/network 2>/dev/null || true
cp -f /etc/config/system $ROOT/etc/config/system 2>/dev/null || true
cp -f $ROOT/usr/share/rpcd/acl.d/*.json /usr/share/rpcd/acl.d/ 2>/dev/null || true
rm -rf $ROOT/tmp/luci-*

# Stop host rpcd so container rpcd handles LuCI sessions against container shadow
/etc/init.d/rpcd stop 2>/dev/null || true
pkill -9 -f "rpcd" 2>/dev/null || true
pkill -f "$ROOT/usr/sbin/uhttpd" 2>/dev/null || true
pkill -f "$ROOT.*dropbear.*2222" 2>/dev/null || true
sleep 1

chroot $ROOT /sbin/rpcd &
sleep 1
chroot $ROOT /usr/sbin/uhttpd -p 8888 -h /www -u /ubus -x /cgi-bin &
chroot $ROOT /usr/sbin/dropbear -p 2222 -R -B &

# Tailscale & AdGuard Home (temporarily disabled, uncomment to enable)
# chroot $ROOT sh -c 'TS_DEBUG_FIREWALL_MODE=iptables TS_NO_LOGS_NO_SUPPORT=true /usr/sbin/tailscaled --port 41641 --state /etc/tailscale/tailscaled.state </dev/null >/tmp/tailscaled.log 2>&1 &'
# chroot $ROOT sh -c '/usr/bin/AdGuardHome -w /var/adguardhome </dev/null >/tmp/adguardhome.log 2>&1 &'

# Set Front Panel LED to Green (System Ready)
echo none > /sys/class/leds/led_r/trigger 2>/dev/null
echo 0 > /sys/class/leds/led_r/brightness 2>/dev/null
echo none > /sys/class/leds/led_b/trigger 2>/dev/null
echo 0 > /sys/class/leds/led_b/brightness 2>/dev/null
echo default-on > /sys/class/leds/led_g/trigger 2>/dev/null
echo 255 > /sys/class/leds/led_g/brightness 2>/dev/null

logger -t openwrt-chroot "OpenWrt services started (LuCI :8888, SSH :2222, Green LED, USB Tethering Drivers)"

# Ensure Wi-Fi persistence
(
    sleep 5
    chroot /proc/1/root ovsh u Wifi_VIF_Config -w if_name==home-ap-24 ssid:='MyHomeWiFi-2.4G' 'wpa_psks:=["map",[["key","YourSecretWiFiPassword"]]]' >/dev/null 2>&1
    chroot /proc/1/root ovsh u Wifi_VIF_Config -w if_name==home-ap-50 ssid:='MyHomeWiFi-5G' 'wpa_psks:=["map",[["key","YourSecretWiFiPassword"]]]' >/dev/null 2>&1
) &
