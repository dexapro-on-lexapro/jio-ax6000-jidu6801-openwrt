# Jio AX6000 (JIDU6801) OpenWrt Dual-Userland & Hardening Playbook

A complete technical reference and repeatable playbook for unlocking, dual-stack provisioning, and hardening the **Jio AX6000 Wi-Fi 6 Router (Model: JIDU6801 / MediaTek Filogic 830 MT7986A)**.

This guide allows an engineer or advanced user to replicate the entire configuration on a fresh router without reverse-engineering vendor subsystems, triggering watchdog factory resets, or losing settings across cold reboots.

---

## 1. Hardware & Architecture Specifications

| Component | Specification |
| :--- | :--- |
| **SoC** | MediaTek MT7986A (Filogic 830) — Quad-core ARM Cortex-A53 @ 2.0 GHz |
| **RAM** | 512 MB DDR3/DDR4 (489 MB usable) |
| **Flash** | 256 MB SPI NAND Flash (`mtd0`, 128 KB erase block size) |
| **Ethernet** | 1x 2.5 GbE / 1 GbE WAN (`eth1`), 4x GbE LAN (`lan1`–`lan4` via `eth0`) |
| **Wi-Fi** | MediaTek MT7976GN / MT7976AN (4x4 2.4 GHz + 4x4 5 GHz Wi-Fi 6 AX6000) |
| **Firmware Architecture** | Vendor OpenWrt base + OpenSync (Plume) mesh + OVSDB + Open vSwitch |
| **Dual Boot** | A/B Slot system (`ubi0:kernel` + `ubi0:rootfs` vs `ubi0:kernel2` + `ubi0:rootfs2`) |

---

## 2. Flash & Partition Layout Reference

The 256 MB SPI NAND (`mtd0`) is segmented into the following MTD and UBI volumes:

```
+-----------------------------------------------------------------------------------+
|                                 mtd0: 256 MB NAND                                 |
+-------+------------+---------+-------+--------------------+----------------+------+
|  BL2  | u-boot-env | Factory |  FIP  |      mtd5: ubi     |   mtd6: ubi2   | Res. |
| (1MB) |  (512KB)   |  (2MB)  | (2MB) |      (140 MB)      |    (90 MB)     | (4MB)|
+-------+------------+---------+-------+---------+----------+----------------+------+
                                                 |                  |
                                     +-----------+-----------+      |
                                     | ubi0: Stock Dual-Slot |      |
                                     | - kernel (Slot 0, 3.8M)      |
                                     | - rootfs (Slot 0, 34M)|      |
                                     | - kernel2 (Slot 1, 3.8M)     |
                                     | - rootfs2 (Slot 1, 37M)      |
                                     | - rootfs_data (Overlay, 8M)  |
                                     | - Idle unallocated (43 MB)   |
                                     +-----------------------+      |
                                                                    v
                                                     +-------------------------------+
                                                     | ubi1: OpenWrt Userland        |
                                                     | - Volume: "openwrt"           |
                                                     | - Size: 90 MB raw / 74MB UBIFS|
                                                     | - Mount: /mnt                 |
                                                     | - LuCI (:8888), Dropbear(:2222|
                                                     +-------------------------------+
```

---

## 3. High-Risk Vendor Traps & Neutralizations

Before making any modifications on a fresh device, **the following three vendor mechanisms must be neutralized**:

### Trap 1: The Anti-Tamper Watchdog (`jDeviceHealthCheck`)
- **Mechanism:** A vendor daemon (`/sbin/jDeviceHealthCheck`) periodically runs SHA256 integrity checks against `/etc/shadow`, `/etc/firewall.user`, and `/etc/mwan3.user`. If discrepancies are detected, it executes:
  ```sh
  jffs2reset -r -y  # Factory wipes /overlay and forces immediate reboot
  ```
- **Neutralization:** Replace `/sbin/jDeviceHealthCheck` on the persistent overlay (`/overlay/upper/sbin/jDeviceHealthCheck`) with a no-op script:
  ```sh
  cat << 'EOF' > /sbin/jDeviceHealthCheck
  #!/bin/sh
  exit 0
  EOF
  chmod +x /sbin/jDeviceHealthCheck
  ```

### Trap 2: The WAN DHCP / 464xlat Reversion (`wanmodechange`)
- **Mechanism:** On boot, `/etc/init.d/wanmodechange` forces `network.wan.proto='dhcp'` and reconfigures `464xlat` carrier tunneling, overriding user static IP and MAC spoof settings in UCI.
- **Neutralization:**
  1. Patch `/etc/init.d/wanmodechange` so it skips enforcing dynamic mode on `wan`.
  2. Install an early boot guard script at `START=16` (`/etc/init.d/jio-wan-dhcp-guard`) that runs right after network initialization and enforces static parameters.

### Trap 3: The Wi-Fi Reversion Mechanism (`pm_gw_offline_store`)
- **Mechanism:** Stock Wi-Fi does **not** read standard `/etc/config/wireless`. It runs OpenSync (`dm`, `wm`, `pm`) with Open vSwitch (`conf.db`). When offline, Platform Manager (`pm`) reads the offline database at `/usr/opensync/data/pm_gw_offline_store` and pushes vendor factory credentials (`NAUSHAD` / `azaan2023`) to OVSDB at boot.
- **Neutralization:**
  1. Patch `/overlay/upper/usr/opensync/data/pm_gw_offline_store` to replace the factory SSID and password.
  2. Install a startup service (`/etc/init.d/jio-wifi-guard`) at `START=99` to issue `wifimethods setApParams` and `ovsh u Wifi_VIF_Config`.

---

## 4. Step-by-Step Deployment Procedure

### Step 1: Initial Root Access
Gain initial shell access (via stock vulnerability, UART, or temporary exploit) on port 22 or serial console.

### Step 2: Neutralize the Health Check Watchdog Immediately
```sh
# Verify watchdog existence
ls -la /sbin/jDeviceHealthCheck

# Replace with harmless dummy
cat << 'EOF' > /sbin/jDeviceHealthCheck
#!/bin/sh
exit 0
EOF
chmod 755 /sbin/jDeviceHealthCheck
```

### Step 3: Format & Provision `mtd6` for OpenWrt Userland
Partition `mtd6` (the unused 90 MB partition) as a dedicated UBI container for full OpenWrt:

```sh
# Detach if attached
ubidetach -m 6 2>/dev/null || true

# Format mtd6 as UBI
ubiformat /dev/mtd6 -y

# Attach mtd6 as ubi1
ubiattach -m 6 -d 1

# Create 'openwrt' volume using maximum available logical eraseblocks
ubimkvol /dev/ubi1 -N openwrt -m

# Format as UBIFS and mount to /mnt
mkdir -p /mnt
mount -t ubifs /dev/ubi1_0 /mnt
```

### Step 4: Deploy OpenWrt RootFS into `/mnt`
Download and extract a rootfs archive for MediaTek Filogic 830 (aarch64_cortex-a53):

```sh
cd /mnt
# Download rootfs (e.g. OpenWrt generic rootfs tar.gz)
wget -O /tmp/openwrt-rootfs.tar.gz https://downloads.openwrt.org/releases/25.12-SNAPSHOT/targets/mediatek/filogic/openwrt-mediatek-filogic-rootfs.tar.gz

# Extract into /mnt
tar -xzf /tmp/openwrt-rootfs.tar.gz -C /mnt/
rm -f /tmp/openwrt-rootfs.tar.gz
```

### Step 5: Configure Ports & Standalone Chroot
Inside the OpenWrt userland (`/mnt`), isolate ports so they don't collide with the stock web UI (which uses port 80/443/8080) or stock dropbear (port 22):

1. **Set LuCI / uhttpd to port `8888`:**
   ```sh
   sed -i "s/list listen_http '0.0.0.0:80'/list listen_http '0.0.0.0:8888'/g" /mnt/etc/config/uhttpd
   sed -i "s/list listen_http '\[::\]:80'/list listen_http '\[::\]:8888'/g" /mnt/etc/config/uhttpd
   ```

2. **Set OpenWrt Dropbear SSH to port `2222`:**
   ```sh
   sed -i "s/option Port '22'/option Port '2222'/g" /mnt/etc/config/dropbear
   ```

3. **Configure Passwordless Initial Access in `/mnt/etc/shadow`:**
   ```sh
   sed -i 's/^root:[^:]*:/root::/' /mnt/etc/shadow
   ```

---

## 5. Startup Automation & Boot Scripts

Create the boot integration scripts on the **host filesystem** so everything starts up automatically after power cycles.

### Script A: `/start-openwrt.sh` (Inside `/mnt` / Persistent Root)
Create `/mnt/start-openwrt.sh` with permissions `755`:

```sh
cat << 'EOF' > /mnt/start-openwrt.sh
#!/bin/sh
ROOT="/mnt"

# Prevent duplicate execution
if netstat -tlpn 2>/dev/null | grep -q ":8888 "; then
    exit 0
fi

# Prepare kernel virtual filesystems
mkdir -p $ROOT/proc $ROOT/sys $ROOT/dev $ROOT/dev/pts $ROOT/tmp
grep -q "$ROOT/proc" /proc/mounts || mount -t proc /proc $ROOT/proc
grep -q "$ROOT/sys" /proc/mounts || mount -t sysfs /sys $ROOT/sys
grep -q "$ROOT/dev " /proc/mounts || mount --bind /dev $ROOT/dev
grep -q "$ROOT/dev/pts" /proc/mounts || mount -t devpts /dev/pts $ROOT/dev/pts
grep -q "$ROOT/tmp" /proc/mounts || mount -t tmpfs -o size=32M tmpfs $ROOT/tmp

mkdir -p $ROOT/tmp/run/ubus
grep -q "$ROOT/tmp/run/ubus" /proc/mounts || mount --bind /var/run/ubus $ROOT/tmp/run/ubus

# Sync host network configs and ACLs into OpenWrt
cp -f /etc/config/wireless $ROOT/etc/config/wireless 2>/dev/null || true
cp -f /etc/config/network $ROOT/etc/config/network 2>/dev/null || true
cp -f /etc/config/system $ROOT/etc/config/system 2>/dev/null || true
cp -f $ROOT/usr/share/rpcd/acl.d/*.json /usr/share/rpcd/acl.d/ 2>/dev/null || true
rm -rf $ROOT/tmp/luci-*

# Stop host rpcd to avoid credential conflict with stock shadow
/etc/init.d/rpcd stop 2>/dev/null || true
pkill -9 -f "rpcd" 2>/dev/null || true
pkill -f "$ROOT/usr/sbin/uhttpd" 2>/dev/null || true
pkill -f "$ROOT.*dropbear.*2222" 2>/dev/null || true
sleep 1

# Start OpenWrt daemons
chroot $ROOT /sbin/rpcd &
sleep 1
chroot $ROOT /usr/sbin/uhttpd -p 8888 -h /www -u /ubus -x /cgi-bin &
chroot $ROOT /usr/sbin/dropbear -p 2222 -R -B &

logger -t openwrt-chroot "OpenWrt services started (LuCI :8888, SSH :2222)"

# Guarantee Wi-Fi settings
(
    sleep 5
    chroot /proc/1/root ubus call wifimethods setApParams '{"APName":"PRIMARY_2G","SSID":"MyHomeWiFi","KeyPassphrase":"YourSecretWiFiPassword"}' >/dev/null 2>&1
    chroot /proc/1/root ubus call wifimethods setApParams '{"APName":"PRIMARY_5G","SSID":"MyHomeWiFi","KeyPassphrase":"YourSecretWiFiPassword"}' >/dev/null 2>&1
) &
EOF
chmod +x /mnt/start-openwrt.sh
```

### Script B: `/etc/init.d/openwrt` (Host Boot Trigger)
On the host, create `/etc/init.d/openwrt` with `START=95`:

```sh
cat << 'EOF' > /etc/init.d/openwrt
#!/bin/sh /etc/rc.common
START=95
STOP=10

boot() {
    start
}

start() {
    grep -q "/mnt ubifs" /proc/mounts || mount -t ubifs /dev/ubi1_0 /mnt 2>/dev/null || true
    [ -x /mnt/start-openwrt.sh ] && /mnt/start-openwrt.sh </dev/null >/dev/null 2>&1 &
}

stop() {
    kill -9 $(netstat -tlpn 2>/dev/null | grep ":8888 " | awk '{print $7}' | cut -d'/' -f1) 2>/dev/null || true
    pkill -f "/mnt.*dropbear.*2222" 2>/dev/null || true
    pkill -f "/mnt/sbin/rpcd" 2>/dev/null || true
}
EOF
chmod +x /etc/init.d/openwrt
ln -sf ../init.d/openwrt /etc/rc.d/S95openwrt
```

### Script C: `/etc/init.d/jio-wan-dhcp-guard` (Static WAN Enforcer)
On the host, create `/etc/init.d/jio-wan-dhcp-guard` with `START=16`:

```sh
cat << 'EOF' > /etc/init.d/jio-wan-dhcp-guard
#!/bin/sh /etc/rc.common
START=16
STOP=90

boot() {
    start
}

start() {
    # Replace with your static IP, Gateway, Netmask, and MAC:
    STATIC_IP="192.168.1.100"
    NETMASK="255.255.255.0"
    GATEWAY="192.168.1.1"
    WAN_MAC="00:11:22:33:44:55"

    # Enforce in UCI
    uci set network.wan.proto='static'
    uci set network.wan.ipaddr="$STATIC_IP"
    uci set network.wan.netmask="$NETMASK"
    uci set network.wan.gateway="$GATEWAY"
    uci set network.wan.macaddr="$WAN_MAC"
    uci set network.wan.device='eth1'
    uci commit network

    # Apply directly to physical interface if already up
    ip link set dev eth1 address "$WAN_MAC" 2>/dev/null || true
    ip addr flush dev eth1 2>/dev/null || true
    ip addr add ${STATIC_IP}/${NETMASK} dev eth1 2>/dev/null || true
    ip link set dev eth1 up 2>/dev/null || true
    ip route replace default via "$GATEWAY" dev eth1 2>/dev/null || true
}
EOF
chmod +x /etc/init.d/jio-wan-dhcp-guard
ln -sf ../init.d/jio-wan-dhcp-guard /etc/rc.d/S16jio-wan-dhcp-guard
```

### Script D: `/etc/init.d/jio-wifi-guard` (Wi-Fi Persistent Enforcer)
On the host, create `/etc/init.d/jio-wifi-guard` with `START=99`:

```sh
cat << 'EOF' > /etc/init.d/jio-wifi-guard
#!/bin/sh /etc/rc.common
START=99
STOP=10

boot() {
    start
}

start() {
    (
        # Wait up to 60s for wifimethods ubus object
        for i in $(seq 1 60); do
            if ubus list | grep -q "^wifimethods$"; then
                break
            fi
            sleep 1
        done
        sleep 2

        # Configure your desired SSID and WPA2 Key:
        SSID_NAME="MyHomeWiFi"
        WIFI_PASS="YourSecretWiFiPassword"

        # Apply via vendor API
        ubus call wifimethods setApParams "{\"APName\":\"PRIMARY_2G\",\"SSID\":\"$SSID_NAME\",\"KeyPassphrase\":\"$WIFI_PASS\"}" >/dev/null 2>&1
        ubus call wifimethods setApParams "{\"APName\":\"PRIMARY_5G\",\"SSID\":\"$SSID_NAME\",\"KeyPassphrase\":\"$WIFI_PASS\"}" >/dev/null 2>&1

        # Enforce in live OVSDB
        ovsh u Wifi_VIF_Config -w if_name==home-ap-24 ssid:="$SSID_NAME" "wpa_psks:=[\"map\",[[\"key\",\"$WIFI_PASS\"]]]" >/dev/null 2>&1
        ovsh u Wifi_VIF_Config -w if_name==home-ap-50 ssid:="$SSID_NAME" "wpa_psks:=[\"map\",[[\"key\",\"$WIFI_PASS\"]]]" >/dev/null 2>&1

        # Synchronize offline flash store
        if [ -f /overlay/upper/usr/opensync/data/pm_gw_offline_store ]; then
            sed -i "s/\"ssid\":\"[^\"]*\"/\"ssid\":\"$SSID_NAME\"/g" /overlay/upper/usr/opensync/data/pm_gw_offline_store
            sed -i "s/\"key--1\",\"[^\"]*\"/\"key--1\",\"$WIFI_PASS\"/g" /overlay/upper/usr/opensync/data/pm_gw_offline_store
        fi
        logger -t jio-wifi-guard "Enforced SSID $SSID_NAME on 2.4G and 5G"
    ) &
}
EOF
chmod +x /etc/init.d/jio-wifi-guard
ln -sf ../init.d/jio-wifi-guard /etc/rc.d/S99jio-wifi-guard
```

---

## 6. Verification & Health Check Commands

Run this one-liner verification script on the router to validate all subsystems:

```sh
echo "=== 1. WATCHDOG STATUS ==="
cat /sbin/jDeviceHealthCheck | grep -E "exit 0|jffs2reset"

echo "=== 2. WAN STATUS ==="
ip a show eth1 | grep -E "link/ether|inet "
ip route | grep default

echo "=== 3. WI-FI STATUS ==="
iw dev | grep -E "Interface|ssid|channel"

echo "=== 4. OPENWRT SERVICES ==="
netstat -tlpn | grep -E "2222|8888"

echo "=== 5. STORAGE SPACE ==="
df -h /mnt /dev/ubi1_0
```

### Expected Output:
- **Watchdog:** Returns `exit 0`.
- **WAN:** MAC matches spoofed address (`00:11:22:33:44:55`), static IP bound, default route via `192.168.1.1`.
- **Wi-Fi:** `home-ap-24` and `home-ap-50` both broadcasting `ssid MyHomeWiFi`.
- **Services:** Port `8888` (uhttpd LuCI) and Port `2222` (Dropbear OpenWrt) listening.
- **Storage:** `/mnt` has ~74 MB total UBIFS capacity with ~47 MB available for package installation.

---

## 7. Web Management & Port Reference

| Portal / Service | URL / Command | Purpose |
| :--- | :--- | :--- |
| **OpenWrt LuCI Web UI** | `http://192.168.31.1/` and `:8888` | Primary router control panel (User: `root`, blank password) |
| **Stock Host SSH** | `ssh root@192.168.31.1` (Port 22) | Stock Linux 5.4 host access |
| **OpenWrt SSH (chroot)** | `ssh -p 2222 root@192.168.31.1` | OpenWrt 25.12 environment with `apk` package manager |
| **AdGuard Home Admin** | `http://192.168.31.1:3000` | AdGuard Home DNS sinkhole dashboard |
| **AdGuard Home DNS** | `192.168.31.1:5335` | Local upstream DNS resolver |
| **Tailscale VPN** | Port 41641 / `tailscale status` | Mesh VPN daemon |
| **Dynamic LED Status** | Front RGB LED Indicator | Real-time connection feedback (Red/Green/Purple/Blue) |

---

## 8. Cold-Boot Resolution Discoveries

1. **`dnsmasq` Bind Conflict Resolution:**
   - **Root Cause:** Procd passed hardcoded `-z` (`--bind-interfaces`) while OpenWrt defaulted `nonwildcard` to 1 (appending `--bind-dynamic`). Dnsmasq threw fatal error: `cannot set --bind-interfaces and --bind-dynamic`.
   - **Fix:** `uci set dhcp.@dnsmasq[0].nonwildcard='0'; uci commit dhcp`.
2. **LuCI Error 500 (`left-hand side expression is null`):**
   - **Root Cause:** In OpenWrt userland, `/var` is a symlink to `/tmp`. When `/tmp` was mounted as a fresh tmpfs, `/var/run/ubus` was empty, causing `ubus call system board` to fail and template rendering to throw an unhandled exception.
   - **Fix:** Mount `/var/run/ubus` from host to `/mnt/tmp/run/ubus`.
3. **Multi-Daemon Chroot Process Launching:**
   - **Root Cause:** `start-stop-daemon -x /usr/sbin/chroot` checked process binary name `chroot` and refused to launch subsequent daemons once `rpcd` was alive.
   - **Fix:** Launch background daemons directly via `chroot $ROOT /path/to/binary [args] &`.
