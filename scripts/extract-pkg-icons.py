#!/usr/bin/env python3
"""Extract package identity metadata and app-specific ICON0.PNG using HTTP ranges.

The catalog never downloads whole PKG files.  For PS4/PS5 CNT packages and PS5
FIH+CNT packages we read only the outer header, CNT entry table, PARAM.SFO and
(optionally) ICON0.PNG.  The resulting title/content identifiers are used by
Packizard Store V21 to correlate catalog entries with installed applications.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import struct
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "packages.json"
ICONS = ROOT / "icons"
RAW_BASE = "https://raw.githubusercontent.com/z3r3lkio/z3shop-catalog/main/icons"
USER_AGENT = "Z3Shop-Catalog-PKGMeta/2.0"
TOKEN = os.environ.get("GITHUB_TOKEN", "")

PKG_MAGIC_CNT = 0x7F434E54  # \x7fCNT
PKG_MAGIC_FIH = 0x7F464948  # \x7fFIH, native PS5 finalized-image wrapper
ENTRY_PARAM_SFO = 0x1000
ENTRY_ICON0_PNG = 0x1200
ENTRY_SIZE = 0x20
HEADER_BYTES = 0x1000
MAX_ENTRIES = 0x10000
MAX_TABLE_BYTES = 2 * 1024 * 1024
MAX_ICON_BYTES = 8 * 1024 * 1024
MAX_SFO_BYTES = 256 * 1024
ICON_SIDE = 256
TARGET_MAX_BYTES = 120 * 1024
TITLE_ID_RE = re.compile(r"(?<![A-Z0-9])([A-Z]{4}[0-9]{5})(?![A-Z0-9])", re.I)


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


def parse_sfo_strings(data: bytes) -> dict[str, str]:
    """Parse bounded UTF-8 string entries from PARAM.SFO."""
    if len(data) < 20 or data[:4] != b"\x00PSF":
        raise ValueError("PARAM.SFO magic/size mismatch")

    key_table_off, data_table_off, entry_count = struct.unpack_from("<III", data, 8)
    if entry_count > 4096:
        raise ValueError(f"implausible PARAM.SFO entry count: {entry_count}")

    result: dict[str, str] = {}
    for index in range(entry_count):
        entry_off = 20 + index * 16
        if entry_off + 16 > len(data):
            break
        key_off, fmt, data_len, _data_max_len, value_off = struct.unpack_from(
            "<HHIII", data, entry_off
        )
        if fmt not in (0x0004, 0x0204):
            continue
        key_abs = key_table_off + key_off
        value_abs = data_table_off + value_off
        value_end = value_abs + data_len
        if key_abs >= len(data) or value_abs > len(data) or value_end > len(data):
            continue
        key_end = data.find(b"\0", key_abs)
        if key_end < 0:
            key_end = len(data)
        key = data[key_abs:key_end].decode("utf-8", "replace").strip()
        value = data[value_abs:value_end].split(b"\0", 1)[0].decode("utf-8", "replace").strip()
        if key and value:
            result[key] = value
    return result


def title_id_from_text(value: str) -> str:
    decoded = urllib.parse.unquote(str(value or ""))
    match = TITLE_ID_RE.search(decoded.upper())
    return match.group(1).upper() if match else ""


def clean_identity(value: str, max_len: int) -> str:
    text = str(value or "").replace("\x00", "").strip()
    if not text or len(text) > max_len:
        return ""
    if any(ord(ch) < 32 or ord(ch) > 126 for ch in text):
        return ""
    return text


def inspect_pkg(url: str, want_icon: bool) -> tuple[bytes | None, dict]:
    outer, final_url = range_fetch(url, 0, HEADER_BYTES)
    magic = u32be(outer, 0)

    if magic == PKG_MAGIC_CNT:
        sc_offset = 0
        cnt_header = outer
        container = "CNT"
    elif magic == PKG_MAGIC_FIH:
        # PS5 FIH is little-endian. FIH+0x58 points at the embedded big-endian CNT.
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

    entries: dict[int, tuple[int, int]] = {}
    for index in range(entry_count):
        off = index * ENTRY_SIZE
        entry_id = u32be(table, off)
        relative = u32be(table, off + 0x10)
        size = u32be(table, off + 0x14)
        if entry_id in (ENTRY_PARAM_SFO, ENTRY_ICON0_PNG):
            entries[entry_id] = (relative, size)

    sfo_values: dict[str, str] = {}
    sfo_entry = entries.get(ENTRY_PARAM_SFO)
    if sfo_entry:
        sfo_relative, sfo_size = sfo_entry
        if 20 <= sfo_size <= MAX_SFO_BYTES:
            sfo, _ = range_fetch(url, sc_offset + sfo_relative, sfo_size)
            sfo_values = parse_sfo_strings(sfo)

    icon: bytes | None = None
    icon_offset = 0
    icon_size = 0
    if want_icon:
        icon_entry = entries.get(ENTRY_ICON0_PNG)
        if not icon_entry:
            raise ValueError("ICON0 entry 0x1200 not present in CNT")
        icon_relative, icon_size = icon_entry
        if icon_size <= 8 or icon_size > MAX_ICON_BYTES:
            raise ValueError(f"invalid ICON0 size: {icon_size}")
        icon_offset = sc_offset + icon_relative
        icon, _ = range_fetch(url, icon_offset, icon_size)
        if not icon.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError(
                f"ICON0 entry is not PNG: first8={icon[:8].hex()} offset=0x{icon_offset:X}"
            )

    return icon, {
        "container": container,
        "icon_entry_id": "0x1200",
        "icon_entry_offset": icon_offset,
        "icon_entry_size": icon_size,
        "cnt_offset": sc_offset,
        "package_final_url": final_url,
        "sfo": sfo_values,
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
        pal = canvas.quantize(
            colors=192,
            method=Image.Quantize.FASTOCTREE,
            dither=Image.Dither.FLOYDSTEINBERG,
        )
        out = io.BytesIO()
        pal.save(out, "PNG", optimize=True, compress_level=9)
        return out.getvalue()


def is_pkg_url(pkg: dict) -> bool:
    url = str(pkg.get("url") or "")
    return url.lower().split("?", 1)[0].endswith(".pkg")


def should_extract_icon(pkg: dict) -> bool:
    if not is_pkg_url(pkg):
        return False
    origin = str(pkg.get("icon_origin") or "").lower()
    source = str(pkg.get("icon_source_url") or "").lower()
    # Exact upstream standalone art is already authoritative and cheaper to refresh.
    return not (origin == "direct" and source)


def enrich_identity(pkg: dict, sfo: dict[str, str], url: str) -> bool:
    changed = False
    title_id = clean_identity(sfo.get("TITLE_ID", ""), 24) or title_id_from_text(url)
    content_id = clean_identity(sfo.get("CONTENT_ID", ""), 64)
    app_version = clean_identity(sfo.get("APP_VER", "") or sfo.get("VERSION", ""), 48)

    if title_id:
        if pkg.get("title_id") != title_id:
            pkg["title_id"] = title_id
            changed = True
    if content_id:
        if pkg.get("content_id") != content_id:
            pkg["content_id"] = content_id
            changed = True
    if app_version:
        if pkg.get("package_version") != app_version:
            pkg["package_version"] = app_version
            changed = True
    if title_id or content_id or app_version:
        if pkg.get("identity_origin") != "param.sfo":
            pkg["identity_origin"] = "param.sfo"
            changed = True
    return changed


def main() -> int:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    packages = catalog.get("packages") or []
    ICONS.mkdir(parents=True, exist_ok=True)

    metadata_attempts = 0
    metadata_resolved = 0
    attempted_icons = 0
    extracted_icons = 0
    cnt_extracted = 0
    fih_extracted = 0

    for pkg in packages:
        pkg_id = str(pkg.get("id") or "").strip()
        url = str(pkg.get("url") or "").strip()
        if not pkg_id or not url.startswith("https://"):
            continue

        # Non-PKG native images often carry a PPSA id in the release asset name.
        # Preserve that reliable identifier even though PARAM.SFO is not directly
        # range-addressable without parsing the filesystem image itself.
        if not is_pkg_url(pkg):
            inferred = title_id_from_text(url)
            if inferred and not pkg.get("title_id"):
                pkg["title_id"] = inferred
                pkg["identity_origin"] = "asset-name"
                metadata_resolved += 1
            continue

        metadata_attempts += 1
        want_icon = should_extract_icon(pkg)
        if want_icon:
            attempted_icons += 1
        try:
            raw_icon, meta = inspect_pkg(url, want_icon)
            if enrich_identity(pkg, meta.get("sfo") or {}, url):
                metadata_resolved += 1
                print(
                    f"[pkg-meta:ok] {pkg_id}: title_id={pkg.get('title_id', '')} "
                    f"content_id={pkg.get('content_id', '')} app_ver={pkg.get('package_version', '')}"
                )
            elif pkg.get("title_id") or pkg.get("content_id"):
                metadata_resolved += 1

            if raw_icon is None:
                continue
            data = optimized_png(raw_icon)
            if len(data) > 220 * 1024:
                raise ValueError(f"optimized ICON0 too large: {len(data)}")
            dest = ICONS / f"{pkg_id}.png"
            dest.write_bytes(data)
            digest = hashlib.sha256(data).hexdigest()
            pkg["icon_url"] = f"{RAW_BASE}/{pkg_id}.png?v={digest[:12]}"
            pkg["icon_source_url"] = f"{url}#entry-0x1200"
            pkg["icon_origin"] = "pkg-entry:0x1200"
            pkg["icon_bytes"] = len(data)
            pkg["icon_pkg_entry_size"] = meta["icon_entry_size"]
            pkg["icon_pkg_container"] = meta["container"]
            extracted_icons += 1
            if meta["container"] == "FIH+CNT":
                fih_extracted += 1
            else:
                cnt_extracted += 1
            print(
                f"[pkg-icon:ok] {pkg_id}: {meta['container']} "
                f"embedded={meta['icon_entry_size']} optimized={len(data)} "
                f"offset=0x{meta['icon_entry_offset']:X}"
            )
        except Exception as exc:
            print(f"[pkg-inspect:warn] {pkg_id}: {type(exc).__name__}: {exc}")
            # Filename/URL identity is still useful when an origin blocks Range.
            inferred = title_id_from_text(url)
            if inferred and not pkg.get("title_id"):
                pkg["title_id"] = inferred
                pkg["identity_origin"] = "asset-name"
                metadata_resolved += 1

    stats = catalog.setdefault("_stats", {})
    stats["pkg_metadata_attempts"] = metadata_attempts
    stats["pkg_metadata_resolved"] = metadata_resolved
    stats["pkg_icon_attempts"] = attempted_icons
    stats["pkg_icons_extracted"] = extracted_icons
    stats["pkg_icons_cnt"] = cnt_extracted
    stats["pkg_icons_fih"] = fih_extracted
    CATALOG.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"PKG metadata: {metadata_resolved}/{metadata_attempts}; "
        f"ICON0 extraction: {extracted_icons}/{attempted_icons} "
        f"(CNT={cnt_extracted}, FIH={fih_extracted})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
