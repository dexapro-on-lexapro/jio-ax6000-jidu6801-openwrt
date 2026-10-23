#!/usr/bin/env python3
"""
MediaTek MT7986 BROM Vanilla / Password-Free U-Boot Bootloader Utility
----------------------------------------------------------------------
Uses the MediaTek BootROM (BROM) protocol over UART to load a RAM BL2,
stream a password-free vanilla U-Boot FIP directly into DDR RAM, and launch it.

This bypasses any vendor U-Boot console password check (such as on JIDU6601/JIDU6801),
giving you direct access to the `MT7986> ` prompt for TFTP booting an initramfs
and flashing clean OpenWrt via sysupgrade.
"""

import os
import sys
import time
import glob
import select
import struct
import argparse
import termios
import tty

DEFAULT_BL2 = os.path.join(os.path.dirname(__file__), "bl2-mt7986-ddr4-ram.bin")
DEFAULT_FIP = os.path.join(os.path.dirname(__file__), "fip-6101-vanilla.bin")
SRAM_LOAD_ADDR = 0x201000

def find_serial_port():
    candidates = glob.glob("/dev/cu.usbserial*") + \
                 glob.glob("/dev/cu.SLAB_USBtoUART*") + \
                 glob.glob("/dev/cu.wchusbserial*") + \
                 glob.glob("/dev/ttyUSB*")
    return candidates[0] if candidates else None

def parse_args():
    parser = argparse.ArgumentParser(description="Boot a password-free Vanilla U-Boot into RAM via MTK BROM")
    parser.add_argument("-p", "--port", default=None, help="Serial port device (auto-detected if omitted)")
    parser.add_argument("-b", "--baud", type=int, default=115200, help="UART baud rate (default: 115200)")
    parser.add_argument("--bl2", default=DEFAULT_BL2, help="Path to RAM BL2 binary (default: bl2-mt7986-ddr4-ram.bin)")
    parser.add_argument("--fip", default=DEFAULT_FIP, help="Path to password-free U-Boot FIP image")
    parser.add_argument("--log", default="brom_boot.log", help="Log output to file (default: brom_boot.log)")
    return parser.parse_args()

class LoggerTee:
    def __init__(self, filename, stream):
        self.file = open(filename, "w", buffering=1)
        self.stream = stream
    def write(self, data):
        self.stream.write(data)
        self.stream.flush()
        try:
            self.file.write(data)
            self.file.flush()
        except Exception:
            pass
    def flush(self):
        self.stream.flush()
        try:
            self.file.flush()
        except Exception:
            pass
    def isatty(self):
        return getattr(self.stream, 'isatty', lambda: False)()
    def fileno(self):
        return self.stream.fileno()

def setup_serial(port_path, baudrate):
    try:
        fd = os.open(port_path, os.O_RDWR | os.O_NOCTTY)
    except Exception as e:
        print(f"[-] Error opening serial port {port_path}: {e}")
        sys.exit(1)

    attr = termios.tcgetattr(fd)
    attr[0] = 0  # iflag
    attr[1] = 0  # oflag
    attr[2] = termios.CS8 | termios.CREAD | termios.CLOCAL  # cflag
    attr[3] = 0  # lflag
    baud_const = getattr(termios, f"B{baudrate}", termios.B115200)
    attr[4] = baud_const  # ispeed
    attr[5] = baud_const  # ospeed
    attr[6][termios.VMIN] = 0
    attr[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, attr)
    termios.tcflush(fd, termios.TCIOFLUSH)
    return fd

def drain_rx(fd):
    time.sleep(0.2)
    termios.tcflush(fd, termios.TCIOFLUSH)
    while True:
        r, _, _ = select.select([fd], [], [], 0.05)
        if fd in r:
            try:
                junk = os.read(fd, 2048)
                if not junk:
                    break
            except Exception:
                break
        else:
            break

def read_exact(fd, n, timeout=2.0):
    buf = bytearray()
    t0 = time.time()
    while len(buf) < n and (time.time() - t0) < timeout:
        r, _, _ = select.select([fd], [], [], 0.02)
        if fd in r:
            try:
                c = os.read(fd, n - len(buf))
                if c:
                    buf.extend(c)
            except Exception:
                pass
    return bytes(buf)

def write_all(fd, data, timeout=2.0):
    p = 0
    t0 = time.time()
    while p < len(data) and (time.time() - t0) < timeout:
        _, w, _ = select.select([], [fd], [], 0.05)
        if fd in w:
            try:
                n = os.write(fd, data[p:])
                p += n
            except Exception:
                pass
    return p == len(data)

def echo(fd, data, timeout=2.0):
    if not write_all(fd, data, timeout):
        raise RuntimeError("Write timeout in BROM echo")
    resp = read_exact(fd, len(data), timeout)
    if resp != data:
        raise RuntimeError(f"Echo mismatch: sent {data.hex()} got {resp.hex()}")

def read_u16(fd):
    b = read_exact(fd, 2)
    if len(b) < 2:
        raise RuntimeError("Timeout reading u16 from device")
    return struct.unpack(">H", b)[0]

def read_u32(fd):
    b = read_exact(fd, 4)
    if len(b) < 4:
        raise RuntimeError("Timeout reading u32 from device")
    return struct.unpack(">I", b)[0]

def fip_checksum(pkt):
    csum = 0
    p = 0
    while len(pkt) - p > 1:
        val = struct.unpack(">H", pkt[p:p+2])[0]
        csum += val
        p += 2
    if len(pkt) != p:
        csum += pkt[-1] << 8
    while csum >> 16:
        csum = (csum >> 16) + (csum & 0xFFFF)
    return csum & 0xFFFF

def send_fip_packet(fd, pidx, pkt):
    echo(fd, struct.pack(">I", pidx))
    echo(fd, struct.pack(">H", len(pkt)))
    cs = fip_checksum(pkt)
    echo(fd, struct.pack(">H", cs))
    write_all(fd, pkt, timeout=5.0)
    ret_idx = read_u32(fd)
    ret_cs = read_u16(fd)
    return ret_idx == pidx and ret_cs == cs

def main():
    args = parse_args()
    port = args.port or find_serial_port()
    if not port:
        print("[-] No serial port detected! Please specify with -p / --port.")
        sys.exit(1)

    sys.stdout = LoggerTee(args.log, sys.stdout)

    print("=" * 68)
    print("  MediaTek MT7986 Vanilla / Password-Free U-Boot BROM Loader")
    print(f"  Port      : {port}")
    print(f"  Baud      : {args.baud}")
    print(f"  RAM BL2   : {args.bl2}")
    print(f"  U-Boot FIP: {args.fip}")
    print("=" * 68)

    if not os.path.exists(args.bl2):
        print(f"[-] Missing BL2 binary: {args.bl2}")
        sys.exit(1)

    if not os.path.exists(args.fip):
        print(f"[-] Missing FIP binary: {args.fip}")
        sys.exit(1)

    fd = setup_serial(port, args.baud)

    # 1. BROM Handshake
    SYNC = [0xA0, 0x0A, 0x50, 0x05]
    print("\n[*] Broadcasting BootROM handshake sequence...")
    print("[*] 👉 Action: Power-cycle the router now (with SPI NAND Pin 2 grounded if cold boot)!")
    sys.stdout.flush()

    drain_rx(fd)
    i = 0
    t_dot = time.time()
    while i < len(SYNC):
        write_all(fd, bytes([SYNC[i]]), timeout=0.05)
        r = read_exact(fd, 1, timeout=0.02)
        if r and r[0] == (0xFF ^ SYNC[i]):
            print(f"\n[+] Caught Sync byte {i}: {SYNC[i]:02X} -> {r[0]:02X} OK!", flush=True)
            i += 1
        else:
            if i != 0:
                i = 0
        time.sleep(0.01)
        if time.time() - t_dot > 1.5:
            sys.stdout.write(".")
            sys.stdout.flush()
            t_dot = time.time()

    print("\n[+] BROM Handshake Complete! Querying SoC parameters...", flush=True)
    drain_rx(fd)

    # 2. Query SoC & Fuse State
    echo(fd, b"\xFD")
    hw_code = read_u16(fd)
    status = read_u16(fd)
    print(f"[+] Hardware Code : 0x{hw_code:04X} (Status: {status})")

    echo(fd, b"\xD8")
    tcfg = read_u32(fd)
    status = read_u16(fd)
    sb = bool(tcfg & 1)
    sla = bool(tcfg & 2)
    daa = bool(tcfg & 4)
    print(f"[+] Security Fuses: TargetConfig=0x{tcfg:08X} (SecureBoot={sb}, SLA={sla}, DAA={daa})")

    if sla or daa:
        print("\n[!] WARNING: SLA / DAA authentication fuses appear active!")
        print("[!] BROM may refuse unsigned BL2 execution.\n")

    # 3. Upload RAM BL2
    bl2_bytes = open(args.bl2, "rb").read()
    print(f"[*] Uploading RAM BL2 ({len(bl2_bytes)} bytes) to SRAM 0x{SRAM_LOAD_ADDR:08X}...")

    echo(fd, b"\xD7")
    echo(fd, struct.pack(">I", SRAM_LOAD_ADDR))
    echo(fd, struct.pack(">I", len(bl2_bytes)))
    echo(fd, struct.pack(">I", 0))
    status = read_u16(fd)
    if status != 0:
        print(f"[-] BROM refused DA init (status: {status})")
        sys.exit(1)

    write_all(fd, bl2_bytes, timeout=10.0)
    csum = read_u16(fd)
    status = read_u16(fd)
    print(f"[+] BL2 upload complete (Checksum: 0x{csum:04X}, Status: {status})")

    # 4. Jump to BL2
    print(f"[*] Jumping to BL2 at 0x{SRAM_LOAD_ADDR:08X} in AArch64 mode...")
    echo(fd, b"\xDE")
    echo(fd, struct.pack(">I", SRAM_LOAD_ADDR))
    echo(fd, b"\x01")  # 64-bit mode
    status = read_u16(fd)
    if status != 0:
        print(f"[-] Jump to 64-bit failed (status: {status})")
        sys.exit(1)

    echo(fd, b"\x64")  # Magic 100
    status = read_u16(fd)
    if status != 0:
        print(f"[-] Jump confirmation failed (status: {status})")
        sys.exit(1)

    print("[+] Jump successful! Waiting for BL2 to initialize DRAM...")

    # 5. Catch BL2 UART Handshake
    banner = bytearray()
    t_banner = time.time()
    while time.time() - t_banner < 10.0:
        c = read_exact(fd, 1, timeout=0.5)
        if c:
            banner.extend(c)
            sys.stdout.write(c.decode("utf-8", errors="replace"))
            sys.stdout.flush()
            if b"Starting UART download handshake" in banner:
                break

    time.sleep(0.1)
    drain_rx(fd)

    REQ = b"mudl"
    RESP = b"TF-A"
    print("\n[*] Synchronizing BL2 UART download protocol...", flush=True)
    for i in range(4):
        req_byte = bytes([REQ[i]])
        resp_char = chr(RESP[i])
        matched = False
        t_step = time.time()
        while not matched and (time.time() - t_step) < 8.0:
            write_all(fd, req_byte, timeout=1.0)
            c = read_exact(fd, 1, timeout=0.5)
            if c:
                sys.stdout.write(c.decode("utf-8", errors="replace"))
                sys.stdout.flush()
                if c[0] == RESP[i]:
                    matched = True
                    print(f" [Matched '{resp_char}']", flush=True)
                    break
            time.sleep(0.05)
        if not matched:
            print(f"\n[-] Timed out waiting for '{resp_char}' from BL2")
            sys.exit(1)

    print("\n[+] BL2 Handshake Complete! Initializing FIP stream...")
    drain_rx(fd)

    # 6. Stream Password-Free FIP Image
    fip_bytes = open(args.fip, "rb").read()
    print(f"[*] Streaming U-Boot FIP ({len(fip_bytes)} bytes) into DDR RAM...")

    echo(fd, b"\x03")
    echo(fd, struct.pack(">I", len(fip_bytes)))
    pkt_idx = 0
    pkt_len = 128
    pos = 0

    t_start = time.time()
    while len(fip_bytes) - pos > pkt_len:
        chunk = fip_bytes[pos:pos+pkt_len]
        if send_fip_packet(fd, pkt_idx, chunk):
            pkt_idx += 1
            pos += pkt_len
            if pkt_len < 32768:
                pkt_len *= 2
            elif pkt_len < 65536 - 1024:
                pkt_len += 1024
            pct = (pos / len(fip_bytes)) * 100
            speed = (pos / (time.time() - t_start + 0.001)) / 1024
            sys.stdout.write(f"\r[*] Transfer Progress: {pct:.1f}% ({speed:.1f} KB/s)")
            sys.stdout.flush()

    while not send_fip_packet(fd, pkt_idx, fip_bytes[pos:]):
        pass

    print(f"\n[+] U-Boot FIP transfer complete! Executing in RAM...")
    echo(fd, b"\x04")  # Go command

    print("\n" + "=" * 68)
    print("  🎉 VANILLA U-BOOT CONSOLE ACTIVE (PASSWORD FREE)!")
    print("  You are now at the raw U-Boot prompt.")
    print("  Commands to load initramfs and flash OpenWrt:")
    print("    MT7986> setenv serverip 192.168.31.2")
    print("    MT7986> setenv ipaddr 192.168.31.1")
    print("    MT7986> tftpboot 0x46000000 openwrt-initramfs.itb")
    print("    MT7986> bootm 0x46000000")
    print("  Press Ctrl+] to exit console.")
    print("=" * 68 + "\n", flush=True)

    # 7. Interactive Serial Terminal
    if sys.stdin.isatty():
        old_term = termios.tcgetattr(sys.stdin)
        tty.setraw(sys.stdin.fileno())
        try:
            while True:
                r, _, _ = select.select([sys.stdin, fd], [], [])
                if sys.stdin in r:
                    ch = os.read(sys.stdin.fileno(), 1024)
                    if not ch or b'\x1d' in ch:  # Ctrl+]
                        break
                    os.write(fd, ch)
                if fd in r:
                    out = os.read(fd, 1024)
                    if not out:
                        break
                    os.write(sys.stdout.fileno(), out)
        finally:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_term)
    else:
        while True:
            r, _, _ = select.select([fd], [], [], 0.1)
            if fd in r:
                chunk = os.read(fd, 1024)
                if chunk:
                    sys.stdout.write(chunk.decode("utf-8", errors="replace"))
                    sys.stdout.flush()

if __name__ == "__main__":
    main()
