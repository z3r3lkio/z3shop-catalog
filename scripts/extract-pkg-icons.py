#!/usr/bin/env python3
"""Extract app-specific ICON0.PNG directly from remote PKG files using HTTP ranges.

Only the PKG header, entry table and ICON0 entry are fetched. Full packages are never
 downloaded. This is used when an upstream repository does not publish standalone art.
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
USER_AGENT = "Z3Shop-Catalog-PKGIcon/1.0"
TOKEN = os.environ.get("GITHUB_TOKEN", "")

PKG_MAGIC = 0x7F434E54
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
            # Refuse a 200 response: an origin that ignores Range could otherwise make
            # the catalog job download an entire multi-gigabyte package.
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


def extract_icon(url: str) -> tuple[bytes, dict]:
    header, final_url = range_fetch(url, 0, HEADER_BYTES)
    if u32be(header, 0) != PKG_MAGIC:
        raise ValueError("bad PKG magic")

    entry_count = u32be(header, 0x10)
    table_offset = u32be(header, 0x18)
    if entry_count <= 0 or entry_count > MAX_ENTRIES:
        raise ValueError(f"unexpected entry count: {entry_count}")
    table_size = entry_count * ENTRY_SIZE
    if table_size <= 0 or table_size > MAX_TABLE_BYTES:
        raise ValueError(f"entry table too large: {table_size}")

    # Reuse header bytes when the complete table is already in the first 4 KiB.
    if table_offset >= 0 and table_offset + table_size <= len(header):
        table = header[table_offset:table_offset + table_size]
    else:
        table, _ = range_fetch(url, table_offset, table_size)

    icon_offset = None
    icon_size = None
    for index in range(entry_count):
        off = index * ENTRY_SIZE
        entry_id = u32be(table, off)
        if entry_id != ENTRY_ICON0_PNG:
            continue
        icon_offset = u32be(table, off + 0x10)
        icon_size = u32be(table, off + 0x14)
        break

    if icon_offset is None or icon_size is None:
        raise ValueError("ICON0 entry 0x1200 not present")
    if icon_size <= 8 or icon_size > MAX_ICON_BYTES:
        raise ValueError(f"invalid ICON0 size: {icon_size}")

    icon, _ = range_fetch(url, icon_offset, icon_size)
    if not icon.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("ICON0 entry is not PNG")

    return icon, {
        "entry_id": "0x1200",
        "entry_offset": icon_offset,
        "entry_size": icon_size,
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
    # Preserve exact/direct upstream artwork. Replace page avatars, legacy cache and
    # missing artwork with the package's own embedded ICON0.
    if origin == "direct" and source:
        return False
    return True


def main() -> int:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    packages = catalog.get("packages") or []
    ICONS.mkdir(parents=True, exist_ok=True)

    attempted = 0
    extracted = 0
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
            extracted += 1
            print(
                f"[pkg-icon:ok] {pkg_id}: embedded={meta['entry_size']} optimized={len(data)} "
                f"offset={meta['entry_offset']}"
            )
        except Exception as exc:
            print(f"[pkg-icon:warn] {pkg_id}: {type(exc).__name__}: {exc}")

    stats = catalog.setdefault("_stats", {})
    stats["pkg_icon_attempts"] = attempted
    stats["pkg_icons_extracted"] = extracted
    CATALOG.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"PKG ICON0 extraction: {extracted}/{attempted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
