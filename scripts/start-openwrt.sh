#!/bin/sh
ROOT="/mnt"

# Prevent duplicate execution if OpenWrt is already running
if ps | grep -v grep | grep -q "$ROOT/usr/sbin/uhttpd"; then
    exit 0
fi

# Ensure UBI1 OpenWrt volume is attached and mounted at /mnt
mkdir -p /mnt
if ! grep -q " /mnt " /proc/mounts; then
    ubiattach -m 6 /dev/ubi_ctrl 2>/dev/null || true
    mount -t ubifs /dev/ubi1_0 /mnt 2>/dev/null || mount -t ubifs ubi1:openwrt /mnt 2>/dev/null || true
fi

[ -d "$ROOT/bin" ] || exit 1

# Ensure USB Tethering kernel modules are loaded
for mod in mii usbnet cdc_ether rndis_host ipheth; do
    grep -q "^$mod " /proc/modules || insmod /lib/modules/5.4.225/$mod.ko 2>/dev/null || true
done

mkdir -p $ROOT/proc $ROOT/sys $ROOT/dev $ROOT/dev/pts $ROOT/tmp $ROOT/tmp/lock $ROOT/tmp/run $ROOT/var/lib/nlbwmon
grep -q "$ROOT/proc" /proc/mounts || mount -t proc /proc $ROOT/proc
grep -q "$ROOT/sys" /proc/mounts || mount -t sysfs /sys $ROOT/sys
grep -q "$ROOT/dev " /proc/mounts || mount --bind /dev $ROOT/dev
grep -q "$ROOT/dev/pts" /proc/mounts || mount -t devpts /dev/pts $ROOT/dev/pts
grep -q "$ROOT/tmp" /proc/mounts || mount -t tmpfs -o size=32M tmpfs $ROOT/tmp

mkdir -p $ROOT/tmp/run/ubus
grep -q "$ROOT/tmp/run/ubus" /proc/mounts || mount --bind /var/run/ubus $ROOT/tmp/run/ubus 2>/dev/null || true

# Ensure active router configurations and ACLs are present
cp -f /etc/config/wireless $ROOT/etc/config/wireless 2>/dev/null || true
cp -f /etc/config/network $ROOT/etc/config/network 2>/dev/null || true
cp -f /etc/config/system $ROOT/etc/config/system 2>/dev/null || true
cp -f /etc/board.json $ROOT/etc/board.json 2>/dev/null || true
cp -f $ROOT/usr/share/rpcd/acl.d/*.json /usr/share/rpcd/acl.d/ 2>/dev/null || true
rm -rf $ROOT/tmp/luci-*

# Ensure host stock uhttpd is running on 8088 / 8443
if ! netstat -tlpn 2>/dev/null | grep -q ":8088 "; then
    /etc/init.d/uhttpd restart >/dev/null 2>&1 || true
fi

# Ensure host stock dropbear is running on 22
if ! netstat -tlpn 2>/dev/null | grep -q ":22 "; then
    /usr/sbin/dropbear -p 22 -R -B >/dev/null 2>&1 || true
fi

# Allow firewall ports
iptables -I INPUT 1 -p tcp -m multiport --dports 22,2222,80,8888,3000,5335,8088,8443 -j ACCEPT 2>/dev/null || true
iptables -I INPUT 1 -p udp -m multiport --dports 53,67,5335 -j ACCEPT 2>/dev/null || true

# Start OpenWrt chroot userland daemons
pkill -f "$ROOT/sbin/rpcd" 2>/dev/null || true
chroot $ROOT /sbin/rpcd >/dev/null 2>&1 &
sleep 1

pkill -f "$ROOT.*uhttpd" 2>/dev/null || true
chroot $ROOT /usr/sbin/uhttpd -f -p 80 -p 8888 -h /www -u /ubus -x /cgi-bin >/dev/null 2>&1 &

pkill -f "$ROOT.*dropbear.*2222" 2>/dev/null || true
chroot $ROOT /usr/sbin/dropbear -F -p 2222 -R -B >/dev/null 2>&1 &

# Bandwidth Monitor
pkill -f "$ROOT/usr/sbin/nlbwmon" 2>/dev/null || true
chroot $ROOT /usr/sbin/nlbwmon -o /var/lib/nlbwmon -p /usr/share/nlbwmon/protocols -s 192.168.31.0/24 -s 192.168.1.0/24 -s 192.168.0.0/16 -r 5s -i 10m >/dev/null 2>&1 &

# AdGuard Home
pkill -f "$ROOT/usr/bin/AdGuardHome" 2>/dev/null || true
mkdir -p $ROOT/var/adguardhome
chroot $ROOT /usr/bin/AdGuardHome -c /etc/adguardhome/adguardhome.yaml -w /var/adguardhome --no-check-update </dev/null >/var/log/adguardhome.log 2>&1 &

# Front RGB LED -> Green (System Ready)
echo none > /sys/class/leds/led_r/trigger 2>/dev/null
echo 0 > /sys/class/leds/led_r/brightness 2>/dev/null
echo none > /sys/class/leds/led_b/trigger 2>/dev/null
echo 0 > /sys/class/leds/led_b/brightness 2>/dev/null
echo default-on > /sys/class/leds/led_g/trigger 2>/dev/null
echo 255 > /sys/class/leds/led_g/brightness 2>/dev/null

logger -t openwrt-chroot "OpenWrt complete services started (Stock UI :8088, LuCI :80/:8888, SSH :22/:2222)"
