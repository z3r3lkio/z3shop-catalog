#!/usr/bin/env python3
"""Extract app-specific ICON0.PNG directly from remote PS4/PS5 PKG files.

Only HTTP byte ranges for the header, embedded CNT entry table and ICON0 entry are
fetched. Full packages are never downloaded. Both standalone CNT packages (0x7FCNT)
and native PS5 FIH packages (0x7FFIH wrapping an embedded CNT) are supported.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import struct
import urllib.request
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "packages.json"
ICONS = ROOT / "icons"
RAW_BASE = "https://raw.githubusercontent.com/z3r3lkio/z3shop-catalog/main/icons"
USER_AGENT = "Z3Shop-Catalog-PKGIcon/1.2"
TOKEN = os.environ.get("GITHUB_TOKEN", "")

PKG_MAGIC_CNT = 0x7F434E54  # \x7fCNT
PKG_MAGIC_FIH = 0x7F464948  # \x7fFIH, native PS5 finalized-image wrapper
ENTRY_ICON0_PNG = 0x1200
ENTRY_SIZE = 0x20
HEADER_BYTES = 0x1000
MAX_ENTRIES = 0x10000
MAX_TABLE_BYTES = 2 * 1024 * 1024
MAX_ICON_BYTES = 8 * 1024 * 1024
ICON_SIDE = 256
TARGET_MAX_BYTES = 120 * 1024


def request_headers(url: str, start: int, end: int) -> dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/octet-stream",
        "Range": f"bytes={start}-{end}",
        "Accept-Encoding": "identity",
    }
    if TOKEN and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {TOKEN}"
    return headers


def range_fetch(url: str, start: int, length: int) -> tuple[bytes, str]:
    if start < 0 or length <= 0:
        raise ValueError("invalid range")
    end = start + length - 1
    request = urllib.request.Request(url, headers=request_headers(url, start, end))
    with urllib.request.urlopen(request, timeout=30) as response:
        status = getattr(response, "status", response.getcode())
        if status != 206:
            # Refuse origins that ignore Range. This prevents accidental full-PKG downloads.
            raise ValueError(f"server did not honor Range (HTTP {status})")
        content_range = response.headers.get("Content-Range", "")
        expected_prefix = f"bytes {start}-{end}/"
        if not content_range.lower().startswith(expected_prefix.lower()):
            raise ValueError(f"unexpected Content-Range: {content_range!r}")
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > length:
            raise ValueError(f"range response too large: {content_length}")
        data = response.read(length + 1)
        if len(data) != length:
            raise ValueError(f"short/oversized range: wanted {length}, got {len(data)}")
        return data, response.geturl()


def u32be(buf: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(buf):
        raise ValueError("u32 outside buffer")
    return struct.unpack_from(">I", buf, offset)[0]


def u64le(buf: bytes, offset: int) -> int:
    if offset < 0 or offset + 8 > len(buf):
        raise ValueError("u64 outside buffer")
    return struct.unpack_from("<Q", buf, offset)[0]


def extract_icon(url: str) -> tuple[bytes, dict]:
    outer, final_url = range_fetch(url, 0, HEADER_BYTES)
    magic = u32be(outer, 0)

    if magic == PKG_MAGIC_CNT:
        sc_offset = 0
        cnt_header = outer
        container = "CNT"
    elif magic == PKG_MAGIC_FIH:
        # PS5 FIH is little-endian. FIH+0x58 points at the embedded big-endian CNT
        # metadata container; system media such as icon0.png live in that CNT.
        sc_offset = u64le(outer, 0x58)
        if sc_offset < 0x10000:
            raise ValueError(f"invalid FIH embedded CNT offset: 0x{sc_offset:X}")
        cnt_header, _ = range_fetch(url, sc_offset, HEADER_BYTES)
        if u32be(cnt_header, 0) != PKG_MAGIC_CNT:
            raise ValueError(
                f"FIH embedded CNT has bad magic 0x{u32be(cnt_header, 0):08X} at 0x{sc_offset:X}"
            )
        container = "FIH+CNT"
    else:
        raise ValueError(f"unsupported PKG magic 0x{magic:08X}; first16={outer[:16].hex()}")

    entry_count = u32be(cnt_header, 0x10)
    table_relative = u32be(cnt_header, 0x18)
    if entry_count <= 0 or entry_count > MAX_ENTRIES:
        raise ValueError(f"unexpected CNT entry count: {entry_count}")
    table_size = entry_count * ENTRY_SIZE
    if table_size <= 0 or table_size > MAX_TABLE_BYTES:
        raise ValueError(f"CNT entry table too large: {table_size}")
    if table_relative < 0x20:
        raise ValueError(f"invalid CNT table offset: 0x{table_relative:X}")

    table_absolute = sc_offset + table_relative
    relative_end = table_relative + table_size
    if relative_end <= len(cnt_header):
        table = cnt_header[table_relative:relative_end]
    else:
        table, _ = range_fetch(url, table_absolute, table_size)

    icon_relative = None
    icon_size = None
    for index in range(entry_count):
        off = index * ENTRY_SIZE
        entry_id = u32be(table, off)
        if entry_id != ENTRY_ICON0_PNG:
            continue
        # CNT entry layout is >6I8x: id, name-offset, flags1, flags2,
        # relative-data-offset, data-size.
        icon_relative = u32be(table, off + 0x10)
        icon_size = u32be(table, off + 0x14)
        break

    if icon_relative is None or icon_size is None:
        raise ValueError("ICON0 entry 0x1200 not present in CNT")
    if icon_size <= 8 or icon_size > MAX_ICON_BYTES:
        raise ValueError(f"invalid ICON0 size: {icon_size}")

    icon_absolute = sc_offset + icon_relative
    icon, _ = range_fetch(url, icon_absolute, icon_size)
    if not icon.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError(
            f"ICON0 entry is not PNG: first8={icon[:8].hex()} offset=0x{icon_absolute:X}"
        )

    return icon, {
        "container": container,
        "entry_id": "0x1200",
        "entry_offset": icon_absolute,
        "entry_relative_offset": icon_relative,
        "entry_size": icon_size,
        "cnt_offset": sc_offset,
        "package_final_url": final_url,
    }


def optimized_png(source: bytes) -> bytes:
    with Image.open(io.BytesIO(source)) as image:
        image.load()
        image = image.convert("RGBA")
        image.thumbnail((ICON_SIDE, ICON_SIDE), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (ICON_SIDE, ICON_SIDE), (0, 0, 0, 0))
        canvas.alpha_composite(image, ((ICON_SIDE - image.width) // 2, (ICON_SIDE - image.height) // 2))
        out = io.BytesIO()
        canvas.save(out, "PNG", optimize=True, compress_level=9)
        data = out.getvalue()
        if len(data) <= TARGET_MAX_BYTES:
            return data
        pal = canvas.quantize(colors=192, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.FLOYDSTEINBERG)
        out = io.BytesIO()
        pal.save(out, "PNG", optimize=True, compress_level=9)
        return out.getvalue()


def should_extract(pkg: dict) -> bool:
    url = str(pkg.get("url") or "")
    if not url.lower().split("?", 1)[0].endswith(".pkg"):
        return False
    origin = str(pkg.get("icon_origin") or "").lower()
    source = str(pkg.get("icon_source_url") or "").lower()
    # Exact upstream standalone art is already authoritative and cheaper to refresh.
    if origin == "direct" and source:
        return False
    return True


def main() -> int:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    packages = catalog.get("packages") or []
    ICONS.mkdir(parents=True, exist_ok=True)

    attempted = 0
    extracted = 0
    cnt_extracted = 0
    fih_extracted = 0
    for pkg in packages:
        if not should_extract(pkg):
            continue
        attempted += 1
        pkg_id = str(pkg.get("id") or "").strip()
        url = str(pkg.get("url") or "").strip()
        if not pkg_id or not url.startswith("https://"):
            continue
        try:
            raw, meta = extract_icon(url)
            data = optimized_png(raw)
            if len(data) > 220 * 1024:
                raise ValueError(f"optimized ICON0 too large: {len(data)}")
            dest = ICONS / f"{pkg_id}.png"
            dest.write_bytes(data)
            digest = hashlib.sha256(data).hexdigest()
            pkg["icon_url"] = f"{RAW_BASE}/{pkg_id}.png?v={digest[:12]}"
            pkg["icon_source_url"] = f"{url}#entry-0x1200"
            pkg["icon_origin"] = "pkg-entry:0x1200"
            pkg["icon_bytes"] = len(data)
            pkg["icon_pkg_entry_size"] = meta["entry_size"]
            pkg["icon_pkg_container"] = meta["container"]
            extracted += 1
            if meta["container"] == "FIH+CNT":
                fih_extracted += 1
            else:
                cnt_extracted += 1
            print(
                f"[pkg-icon:ok] {pkg_id}: {meta['container']} embedded={meta['entry_size']} "
                f"optimized={len(data)} offset=0x{meta['entry_offset']:X}"
            )
        except Exception as exc:
            print(f"[pkg-icon:warn] {pkg_id}: {type(exc).__name__}: {exc}")

    stats = catalog.setdefault("_stats", {})
    stats["pkg_icon_attempts"] = attempted
    stats["pkg_icons_extracted"] = extracted
    stats["pkg_icons_cnt"] = cnt_extracted
    stats["pkg_icons_fih"] = fih_extracted
    CATALOG.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"PKG ICON0 extraction: {extracted}/{attempted} (CNT={cnt_extracted}, FIH={fih_extracted})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
