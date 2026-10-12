#!/usr/bin/env python3
"""
Patch official NullMoth NVIDIA driver payload with hardware fixes for AMD Ryzen + RTX 3060.
- NVRMFB: Lower minimum BAR1 aperture threshold from 256MB to 64MB across all checks
- NVAccel: Allow sub-surface transactions in performTransaction
- NVMTLDriver: Install confirmed crash-safe linear material filter driver for RTX 3060
- nvrm610.conf: Enable NVMTL_WS_LARGE_SURFACE_SHARE_ONLY=1 and NVMTL_STATIC_FIXED_LINEAR=1
- Update SHA256SUMS so install.sh verifies cleanly
"""

import os
import sys
import hashlib
import shutil
import subprocess

def patch_unique(buf: bytearray, target: bytes, repl: bytes, name: str, required: bool = True) -> bool:
    count = buf.count(target)
    if count == 0:
        if required:
            print(f"[-] {name}: pattern not found!")
            return False
        else:
            print(f"[i] {name}: pattern not present in this variant (skipping)")
            return True
    if count > 1:
        print(f"[-] {name}: ambiguous match ({count} times)!")
        return False
    idx = buf.index(target)
    assert len(target) == len(repl), f"{name}: length mismatch ({len(target)} vs {len(repl)})"
    buf[idx:idx+len(repl)] = repl
    print(f"[+] {name}: patched at offset {hex(idx)}")
    return True

def patch_nvrmfb(path: str) -> bool:
    print(f"[*] Patching NVRMFB binary: {path}")
    with open(path, "rb") as f:
        buf = bytearray(f.read())

    ok = True
    ok &= patch_unique(buf, b"\x48\x3d\xff\xff\xff\x0f\x76", b"\x48\x3d\xff\xff\xff\x03\x76", "NVRMFB surveyBar1 256MB->64MB")
    ok &= patch_unique(buf, b"\x48\x3d\x00\x00\x00\x10\x0f\x82", b"\x48\x3d\x00\x00\x00\x04\x0f\x82", "NVRMFB kapiInit 256MB->64MB")
    ok &= patch_unique(buf, b"\x48\x3d\x00\x00\x00\x10\x72", b"\x48\x3d\x00\x00\x00\x04\x72", "NVRMFB discoverBar1 256MB->64MB")

    if ok:
        with open(path, "wb") as f:
            f.write(buf)
        print(f"[+] Successfully wrote patched NVRMFB: {path}")
    return ok

def patch_nvaccel(path: str) -> bool:
    print(f"[*] Patching NVAccel binary: {path}")
    with open(path, "rb") as f:
        buf = bytearray(f.read())

    ok = True
    ok &= patch_unique(buf,
        b"\x48\x8b\x5d\xb8\x0f\x85\x92\x03\x00\x00",
        b"\x48\x8b\x5d\xb8\x0f\x87\x92\x03\x00\x00",
        "NVAccel performTransaction w bounds check", required=True)

    ok &= patch_unique(buf,
        b"\x39\xc3\x0f\x85\x8a\x03\x00\x00",
        b"\x39\xc3\x0f\x87\x8a\x03\x00\x00",
        "NVAccel performTransaction h bounds check", required=True)

    # Optional multihead and copy limit checks (present in specific compiler versions)
    patch_unique(buf,
        b"\x8b\x35\x48\x22\x01\x00\x8d\x46\xff\x85\xc6\x0f\x84\x50\x01\x00\x00",
        b"\x8b\x35\x48\x22\x01\x00\x8d\x46\xff\x85\xc6\xe9\x51\x01\x00\x00\x90",
        "NVAccel FlipTo multihead relax", required=False)

    patch_unique(buf,
        b"\x83\x3d\xd1\x2d\x01\x00\x77\x0f\x87\x8c\x00\x00\x00",
        b"\x83\x3d\xd1\x2d\x01\x00\x77\x90\x90\x90\x90\x90\x90",
        "NVAccel 120-frame limit removal", required=False)

    patch_unique(buf,
        b"\x8b\x35\x6e\x21\x01\x00\x8d\x46\xff\x85\xc6\x0f\x84\xea\x00\x00\x00",
        b"\x8b\x35\x6e\x21\x01\x00\x8d\x46\xff\x85\xc6\xe9\xeb\x00\x00\x00\x90",
        "NVAccel CopyToScanout multihead relax", required=False)

    if ok:
        with open(path, "wb") as f:
            f.write(buf)
        print(f"[+] Successfully wrote patched NVAccel: {path}")
    return ok

def update_nvmtl_driver(pkgroot: str, script_dir: str):
    rtx_driver = os.path.join(script_dir, "rtx3060/NVMTLDriver")
    if os.path.isfile(rtx_driver):
        target = os.path.join(pkgroot, "Library/GPUBundles/NVMTLDriver.bundle/Contents/MacOS/NVMTLDriver")
        if os.path.exists(target):
            print(f"[*] Installing confirmed working RTX 3060 NVMTLDriver from {rtx_driver}")
            shutil.copy2(rtx_driver, target)
            return True
    return False

def update_nvrm_config(pkgroot: str):
    conf_path = os.path.join(pkgroot, "Library/GPUBundles/nvmtl/nvrm610.conf")
    print(f"[*] Updating driver configuration at {conf_path}...")
    settings = {
        "NVMTL_WS_LARGE_SURFACE_SHARE_ONLY": "1",
        "NVMTL_STATIC_FIXED_LINEAR": "1",
        "NVMTL_VRAM_WS_NONIMAGE_MB": "0",
        "NVMTL_VRAM_HEADROOM_MB": "256",
        "NVMTL_RES2_WS": "0",
        "NVMTL_GSUB_HOSTPTR_LIFT": "1",
        "NVMTL_NAK_ARR_SCRATCH": "0",
        "NVMTL_NO_NVENC": "1",
        "NVMTL_NO_NVENC_HEVC": "1",
    }
    lines = []
    if os.path.exists(conf_path):
        with open(conf_path, "r") as f:
            for l in f:
                k = l.split("=")[0].strip()
                if k not in settings:
                    lines.append(l)
    for k, v in settings.items():
        lines.append(f"{k}={v}\n")

    os.makedirs(os.path.dirname(conf_path), exist_ok=True)
    with open(conf_path, "w") as f:
        f.writelines(lines)
    print(f"[+] Written confirmed working settings to {conf_path}")

def update_checksums(pkgroot: str):
    sums_file = os.path.join(pkgroot, "SHA256SUMS")
    if not os.path.exists(sums_file):
        print(f"[-] {sums_file} does not exist, cannot update")
        return False

    print(f"[*] Updating {sums_file}...")
    with open(sums_file, "r") as f:
        lines = f.readlines()

    new_lines = []
    for line in lines:
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2:
            continue
        rel_path = parts[1]
        full_path = os.path.join(pkgroot, rel_path)
        if os.path.isfile(full_path):
            with open(full_path, "rb") as target_f:
                h = hashlib.sha256(target_f.read()).hexdigest()
            new_lines.append(f"{h}  {rel_path}\n")
        else:
            new_lines.append(line)

    with open(sums_file, "w") as f:
        f.writelines(new_lines)
    print(f"[+] {sums_file} updated successfully ({len(new_lines)} entries)")
    return True

def main():
    if len(sys.argv) < 2:
        print("Usage: patch_package.py <pkgroot_directory>")
        sys.exit(1)

    pkgroot = os.path.abspath(sys.argv[1])
    if not os.path.isdir(pkgroot):
        print(f"Error: {pkgroot} is not a directory")
        sys.exit(1)

    script_dir = os.path.dirname(os.path.abspath(__file__))

    # 1. Patch NVRMFB
    nvrmfb_path = os.path.join(pkgroot, "Library/Extensions/NVRMFB.kext/Contents/MacOS/NVRMFB")
    if os.path.exists(nvrmfb_path):
        if not patch_nvrmfb(nvrmfb_path):
            sys.exit(1)

    # 2. Patch NVAccel (both in Extensions and cached variants)
    for rel in [
        "Library/Extensions/NVAccel.kext/Contents/MacOS/NVAccel",
        "Library/NullMoth/kexts/15/NVAccel.kext/Contents/MacOS/NVAccel",
    ]:
        p = os.path.join(pkgroot, rel)
        if os.path.exists(p):
            if not patch_nvaccel(p):
                sys.exit(1)

    # 3. Replace NVMTLDriver with confirmed working RTX 3060 binary
    update_nvmtl_driver(pkgroot, script_dir)

    # 4. Replace libnvmtl_translate.dylib if a custom built one is provided
    custom_xlate = os.environ.get("CUSTOM_XLATE")
    if not custom_xlate:
        default_xlate = os.path.expanduser("~/nvmtl-build/LIVE/out/libnvmtl_translate.dylib")
        if os.path.isfile(default_xlate):
            custom_xlate = default_xlate

    if custom_xlate and os.path.isfile(custom_xlate):
        dest_xlate = os.path.join(pkgroot, "Library/GPUBundles/nvmtl/libnvmtl_translate.dylib")
        if os.path.exists(dest_xlate):
            print(f"[*] Overwriting {dest_xlate} with custom built translator from {custom_xlate}")
            shutil.copy2(custom_xlate, dest_xlate)

    # 5. Write confirmed working configuration
    update_nvrm_config(pkgroot)

    # 6. Ad-hoc codesign on macOS if codesign tool is present
    for binary_rel in [
        "Library/Extensions/NVRMFB.kext/Contents/MacOS/NVRMFB",
        "Library/Extensions/NVAccel.kext/Contents/MacOS/NVAccel",
        "Library/NullMoth/kexts/15/NVAccel.kext/Contents/MacOS/NVAccel",
        "Library/GPUBundles/NVMTLDriver.bundle/Contents/MacOS/NVMTLDriver",
    ]:
        bp = os.path.join(pkgroot, binary_rel)
        if os.path.exists(bp):
            try:
                subprocess.run(["codesign", "-f", "-s", "-", bp], check=False)
            except Exception:
                pass

    # 7. Recompute SHA256SUMS
    if not update_checksums(pkgroot):
        sys.exit(1)

    print("[+] Packaging patch routine completed successfully!")

if __name__ == "__main__":
    main()
