# Autonomous AI Playbook: Jio AX6000 (JIDU6801) Dual-Userland OpenWrt Setup

> **Target Audience:** Autonomous Coding Agents / Engineers  
> **Target Hardware:** Jio AX6000 Wi-Fi 6 Router (Model: `JIDU6801` / MediaTek Filogic 830 `MT7986A` / ODM: Telpa / Gemtek)  
> **Firmware Version:** `GMOBJIO_JIDU6801_R3.0.1`  
> **Workspace Directory:** `.`  
> **Goal:** Take a router from a fresh factory-reset state and autonomously unlock root SSH, neutralize watchdogs, install OpenWrt 25.12 userland onto `mtd6`, modify stock firmware with WAN configuration & theme customizers, and establish cold-boot persistence without bricking or breaking DHCP.

---

## Architecture & System Overview

The system operates as a **Dual-Userland Coexistence Architecture**:
1. **Stock Linux 5.4 Kernel + Hardware Drivers:** Preserves MediaTek proprietary Wi-Fi 6 (`mt7915e` / `mt76`) calibration, beamforming, switch hardware acceleration, and VoIP stack.
2. **Stock Host Services:** Runs stock `uhttpd` on **Port 8088 (HTTP)** and **Port 8443 (HTTPS)** with injected WAN & theme customizer portals, stock `dropbear` on **Port 22**, and stock `dnsmasq` for LAN DHCP.
3. **OpenWrt 25.12 Chroot (`mtd6` / `/mnt`):** Runs on dedicated NAND storage with LuCI Web UI on **Port 80** and **Port 8888**, OpenWrt `dropbear` on **Port 2222**, and AdGuard Home on **Port 3000** (UI) and **Port 5335** (local DNS sinkhole).

```
+-----------------------------------------------------------------------------------------+
|                                  Jio AX6000 Host (Kernel 5.4)                           |
|  - LAN IP: 192.168.31.1 (br-lan, HW MAC: 00:11:22:33:44:56)                           |
|  - WAN IP: Dynamic DHCP / Static / PPPoE / 464XLAT (eth1)                              |
|  - Stock dropbear SSH: Port 22                                                          |
|  - Stock dnsmasq: Port 67 UDP (DHCP Server 192.168.31.2 - .254)                         |
|  - Stock uhttpd: Port 8088 / 8443 (Dashboard, WAN Setup, Theme Customizer)             |
+-----------------------------------------------------------------------------------------+
                                             |
                   +-------------------------+-------------------------+
                   |                                                   |
                   v                                                   v
+-------------------------------------+             +-------------------------------------+
|      OpenWrt 25.12 Userland         |             |      Neutralized Vendor Traps       |
|      (mtd6 / ubi1_0 / /mnt)         |             |                                     |
|  - LuCI Web UI: Port 80 & 8888      |             |  1. /sbin/jDeviceHealthCheck:       |
|  - OpenWrt dropbear SSH: Port 2222  |             |     Neutered with 'exit 0'          |
|  - AdGuard Home: Port 3000 & 5335   |             |  2. OpenSync / Plume telemetry:     |
|  - Bandwidth Monitor: nlbwmon       |             |     Terminated (dm, wm, sm, etc.)   |
|  - Package Manager: apk             |             |  3. br-lan MAC:                     |
+-------------------------------------+             |     Locked to factory hardware MAC  |
                                                    +-------------------------------------+
```

---

## Phase 1: Setting Up the Device From Factory Reset Condition

### 1.1 Physical & Network Topology
1. Connect an Ethernet cable from the Mac/PC to any LAN port (**LAN1–LAN4**) on the router.
2. Confirm the network interface on Mac (e.g. `en5`):
   ```bash
   ifconfig en5
   ```
3. By default, after a factory reset, the router's LAN interface is at **`192.168.31.1`** (or in rare slot variants, `192.168.29.1`).
4. Ensure your Mac Ethernet adapter acquires an IP or set it manually if DHCP is still initializing:
   ```bash
   sudo ifconfig en5 inet 192.168.31.129 netmask 255.255.255.0
   ```
5. Verify ping to the router:
   ```bash
   ping -c 2 192.168.31.1
   ```

### 1.2 Default Factory Credentials
- **Web Interface:** `https://192.168.31.1/` (HTTP on port 80 redirects to HTTPS on port 443)
- **Web Username:** `admin`

---

## Phase 2: Obtaining Initial Root Shell Access

Before provisioning the OpenWrt userland, you must obtain an initial interactive root shell on the router.

### Methods:
1. **UART / Serial Console:** Connect a 3.3V USB-to-UART adapter to the internal serial header (115200 8N1) and log in to the shell or bootloader.
2. **Existing SSH Access:** If Dropbear is already running or enabled on Port 22, log in as `root`:
   ```bash
   ssh root@192.168.31.1
   ```

### 2.1 Verify Root SSH Access
Clear any stale host keys and connect:
```bash
ssh-keygen -R 192.168.31.1 2>/dev/null
ssh -o StrictHostKeyChecking=accept-new root@192.168.31.1 "id; uname -a"
```
*Expected result:* `uid=0(root) gid=0(root)` and Linux Kernel `5.4.225`.

### 2.2 Make Host SSH Persistent
In stock UCI, Dropbear is set to disabled (`option enable '0'`). Permanently enable it:
```bash
ssh root@192.168.31.1 "uci set dropbear.@dropbear[0].enable='1' && uci commit dropbear"
```

---

## Phase 3: Neutralizing Vendor Traps & Hardening

Before performing any customizations, **the following three critical traps must be neutralized**:

### Trap 1: The Anti-Tamper Factory Reset Watchdog
- **The Danger:** A stock daemon `/sbin/jDeviceHealthCheck` runs every 5 minutes and computes SHA256 hashes of `/etc/shadow`, `/etc/firewall.user`, etc. If modified, it triggers:
  ```sh
  jffs2reset -r -y  # Wipes /overlay completely and reboots to factory default!
  ```
- **Neutralization:**
  Replace `/sbin/jDeviceHealthCheck` with a permanent dummy script:
  ```bash
  ssh root@192.168.31.1 "cat << 'EOF' > /sbin/jDeviceHealthCheck
  #!/bin/sh
  exit 0
  EOF
  chmod +x /sbin/jDeviceHealthCheck"
  ```
  Terminate vendor telemetry daemons:
  ```bash
  ssh root@192.168.31.1 "killall -9 dm wm sm qm nm cm fcm om bm vpnm fsm psm jm um ipmond 2>/dev/null || true"
  ```

### Trap 2: The LAN MAC Address Trap
- **The Danger:** Never run `ip link set dev br-lan address ...` with a spoofed MAC. Changing `br-lan`'s MAC breaks the hardware switch FDB learning table on MT7986 and causes client ARP tables to reject DHCP replies.
- **Rule:** Keep `br-lan` at its physical factory MAC (`00:11:22:33:44:56`). Only spoof WAN (`eth1`) if required by your ISP.

### Trap 3: The Full Network Reload Trap
- **The Danger:** Never execute `/etc/init.d/network reload` or `ubus call network reload` when applying WAN settings. In stock OpenWrt, `netifd` rebuilds all bridges, flushing ephemeral aliases on `br-lan` and restarting `dnsmasq`, causing connected devices to lose DHCP.
- **Rule:** Always apply WAN updates with `ifup wan` (or `ubus call network.interface.wan up`).

---

## Phase 4: Installing Userland OpenWrt (`mtd6` / `/mnt`)

The router's 256 MB NAND has `mtd6` (90 MB raw NAND) designated for secondary UBI storage (`ubi2`). We format this partition into `ubi1` containing a 74 MB UBIFS volume named `openwrt`.

### 4.1 Mount Storage & Extract Rootfs
If `mtd6` is not already formatted:
```bash
ssh root@192.168.31.1 << 'EOF'
ubiattach -m 6 /dev/ubi_ctrl 2>/dev/null || true
if ! grep -q " /mnt " /proc/mounts; then
    mkdir -p /mnt
    mount -t ubifs /dev/ubi1_0 /mnt 2>/dev/null || mount -t ubifs ubi1:openwrt /mnt 2>/dev/null || {
        ubiformat /dev/mtd6 -y
        ubiattach -m 6 /dev/ubi_ctrl
        ubimkvol /dev/ubi1 -N openwrt -m
        mount -t ubifs /dev/ubi1_0 /mnt
    }
fi
EOF
```

If deploying the rootfs tarball for the first time:
```bash
scp ./openwrt-rootfs.tar.gz root@192.168.31.1:/tmp/
ssh root@192.168.31.1 "tar -xzf /tmp/openwrt-rootfs.tar.gz -C /mnt/ && rm /tmp/openwrt-rootfs.tar.gz"
```

### 4.2 The Chroot Startup Script (`/mnt/start-openwrt.sh`)
Deploy the hardened chroot master startup script:
Local file: [`scripts/start-openwrt.sh`](file://./scripts/start-openwrt.sh)

Upload to router:
```bash
ssh root@192.168.31.1 "cat > /mnt/start-openwrt.sh" < ./scripts/start-openwrt.sh
ssh root@192.168.31.1 "chmod +x /mnt/start-openwrt.sh"
```

#### What `/mnt/start-openwrt.sh` Does:
1. Mounts kernel pseudo-filesystems into chroot: `/proc`, `/sys`, `/dev`, `/dev/pts`, `/tmp`.
2. Bind-mounts host ubus socket: `/var/run/ubus` -> `/mnt/tmp/run/ubus`.
3. Synchronizes user accounts, network configs, and rpcd ACLs.
4. Ensures host stock `uhttpd` is active on Port 8088 / 8443.
5. Ensures host stock `dropbear` is active on Port 22.
6. Starts OpenWrt chroot `rpcd`.
7. Starts OpenWrt chroot `uhttpd` on **Port 80** and **Port 8888**.
8. Starts OpenWrt chroot `dropbear` on **Port 2222**.
9. Starts Bandwidth Monitor (`nlbwmon`) and AdGuard Home (**Port 3000** & **Port 5335**).
10. Sets Front RGB LED to solid **Green** (indicating complete dual-userland ready).

Execute to launch the OpenWrt environment:
```bash
ssh root@192.168.31.1 "/mnt/start-openwrt.sh"
```

---

## Phase 5: Modifying Stock Firmware (WAN Companion & Themes)

To provide an alternative to LuCI for configuring WAN and custom themes, we install lightweight companion pages directly into the stock `uhttpd` document root (`/www/`).

### 5.1 Reconfigure Stock uhttpd to Port 8088 & 8443
Liberate Port 80 for OpenWrt LuCI while keeping the stock web server running:
```bash
ssh root@192.168.31.1 << 'EOF'
uci -q delete uhttpd.main.listen_http
uci -q delete uhttpd.main.listen_https
uci add_list uhttpd.main.listen_http='0.0.0.0:8088'
uci add_list uhttpd.main.listen_https='0.0.0.0:8443'
uci set uhttpd.main.redirect_https='0'
uci commit uhttpd
/etc/init.d/uhttpd restart
EOF
```

### 5.2 Deploy Frontend Files & Stylesheets
The companion files are organized locally under `web/`:
- [`web/wan-settings.html`](file://./web/wan-settings.html): Standalone WAN configuration page styled with native Jio fonts.
- [`web/theme-settings.html`](file://./web/theme-settings.html): Interactive theme selector with 8 palettes and 1-click live preview.
- [`web/themes/theme-loader.js`](file://./web/themes/theme-loader.js): Loads active theme CSS variables and injects a 5-portal navigation header into the stock dashboard.
- [`web/index.html`](file://./web/index.html): Patched stock entrypoint.
- [`web/themes/*.css`](file://./web/themes): 8 stylesheets (`catppuccin.css`, `dracula.css`, `nord.css`, `tokyo-night.css`, `one-dark.css`, `cyberpunk.css`, `solarized-dark.css`, `classic.css`).

Deploy to the router:
```bash
ssh root@192.168.31.1 "[ -f /www/index.html.stock ] || cp -a /www/index.html /www/index.html.stock"
ssh root@192.168.31.1 "cat > /www/wan-settings.html" < ./web/wan-settings.html
ssh root@192.168.31.1 "cat > /www/theme-settings.html" < ./web/theme-settings.html
ssh root@192.168.31.1 "mkdir -p /www/themes /www/cgi-bin"
tar -C ./web/themes -cf - . | ssh root@192.168.31.1 "tar -C /www/themes -xf -"
ssh root@192.168.31.1 "cat > /www/index.html" < ./web/index.html
```

### 5.3 Deploy Backend CGI Scripts
- [`web/cgi-bin/wan-config`](file://./web/cgi-bin/wan-config): Lua CGI endpoint handling `GET` (live stats) and `POST` (UCI `network.wan` update + `ifup wan`).
- [`web/cgi-bin/theme-api`](file://./web/cgi-bin/theme-api): Lua CGI endpoint saving the active theme to `/www/themes/active-theme.css`.

Deploy and mark executable:
```bash
ssh root@192.168.31.1 "cat > /www/cgi-bin/wan-config" < ./web/cgi-bin/wan-config
ssh root@192.168.31.1 "cat > /www/cgi-bin/theme-api" < ./web/cgi-bin/theme-api
ssh root@192.168.31.1 "chmod +x /www/cgi-bin/wan-config /www/cgi-bin/theme-api"
```

---

## Phase 6: Cold-Boot Persistence Setup

To ensure all OpenWrt services, companion web portals, and custom network settings survive reboots without manual intervention:

### 6.1 Install the OpenWrt Boot Service (`/etc/init.d/openwrt`)
Local file: [`scripts/init-custom/openwrt`](file://./scripts/init-custom/openwrt)

Install and enable at priority `START=95`:
```bash
ssh root@192.168.31.1 "cat > /etc/init.d/openwrt" < ./scripts/init-custom/openwrt
ssh root@192.168.31.1 "chmod +x /etc/init.d/openwrt && /etc/init.d/openwrt enable"
```

### 6.2 Install the Dynamic WAN Guard (`/etc/init.d/jio-wan-guard`)
Local file: [`scripts/init-custom/jio-wan-guard`](file://./scripts/init-custom/jio-wan-guard)

Install and enable at priority `START=16`:
```bash
ssh root@192.168.31.1 "cat > /etc/init.d/jio-wan-guard" < ./scripts/init-custom/jio-wan-guard
ssh root@192.168.31.1 "chmod +x /etc/init.d/jio-wan-guard && /etc/init.d/jio-wan-guard enable"
```

---

## Phase 7: Verification & Health Check Checklist

Run this command sequence from your Mac to verify the entire system:

```bash
echo "=== 1. WATCHDOG STATUS ==="
ssh root@192.168.31.1 "cat /sbin/jDeviceHealthCheck"
# EXPECTED: #!/bin/sh \n exit 0

echo "=== 2. PERSISTENT SSH STATUS ==="
ssh root@192.168.31.1 "uci get dropbear.@dropbear[0].enable"
# EXPECTED: 1

echo "=== 3. OPEN PORTS ==="
ssh root@192.168.31.1 "netstat -tlpn | grep -E '80|8088|8888|22|2222|3000|5335'"
# EXPECTED:
# tcp 0.0.0.0:80    -> chroot uhttpd (LuCI)
# tcp 0.0.0.0:8888  -> chroot uhttpd (LuCI)
# tcp 0.0.0.0:8088  -> host uhttpd (Stock UI)
# tcp 0.0.0.0:22    -> host dropbear
# tcp 0.0.0.0:2222  -> chroot dropbear
# tcp :::3000       -> chroot AdGuard Home
# tcp :::5335       -> chroot AdGuard Home DNS

echo "=== 4. DHCP STATUS ==="
ssh root@192.168.31.1 "ps | grep dnsmasq; cat /tmp/dhcp.leases"
# EXPECTED: dnsmasq active, leasing 192.168.31.x to clients

echo "=== 5. WEB PORTAL HTTP CHECKS ==="
curl -sI http://192.168.31.1/ | head -n 1
curl -sI http://192.168.31.1:8888/ | head -n 1
curl -sI http://192.168.31.1:8088/ | head -n 1
curl -sI http://192.168.31.1:8088/wan-settings.html | head -n 1
curl -sI http://192.168.31.1:8088/theme-settings.html | head -n 1
curl -sI http://192.168.31.1:3000/ | head -n 1
# ALL MUST RETURN: HTTP/1.1 200 OK
```

---

## File Reference Index

| File Path (Local / Router) | Purpose |
| :--- | :--- |
| [`scripts/start-openwrt.sh`](file://./scripts/start-openwrt.sh) | Master chroot mounting & service supervisor |
| [`scripts/init-custom/openwrt`](file://./scripts/init-custom/openwrt) | Procd init service (`START=95`) for boot persistence |
| [`scripts/init-custom/jio-wan-guard`](file://./scripts/init-custom/jio-wan-guard) | Procd init service (`START=16`) for dynamic WAN persistence |
| [`web/wan-settings.html`](file://./web/wan-settings.html) | Stock companion WAN setup frontend |
| [`web/theme-settings.html`](file://./web/theme-settings.html) | Stock companion theme selector frontend |
| [`web/themes/theme-loader.js`](file://./web/themes/theme-loader.js) | Dynamic CSS variable theme injector & top navbar |
| [`web/cgi-bin/wan-config`](file://./web/cgi-bin/wan-config) | Backend Lua CGI for WAN configuration & isolated `ifup` |
| [`web/cgi-bin/theme-api`](file://./web/cgi-bin/theme-api) | Backend Lua CGI for reading & persisting active theme |
| [`/sbin/jDeviceHealthCheck`](file:///sbin/jDeviceHealthCheck) (Router) | Overwritten watchdog script (`exit 0`) |
