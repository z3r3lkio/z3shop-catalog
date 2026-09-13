#!/usr/bin/env python3
"""Remove icon mappings that are not proven to be app-specific artwork.

The resolver may encounter generic author avatars or legacy cache files from an older
heuristic. A missing icon is preferable to displaying the wrong app artwork.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "packages.json"
ICONS = ROOT / "icons"


def untrusted(pkg: dict) -> bool:
    source = str(pkg.get("icon_source_url") or "").lower()
    origin = str(pkg.get("icon_origin") or "").lower()
    if source == "cached" or origin == "cached":
        return True
    if "/storage/users/" in source and "/avatar." in source:
        return True
    if "nexgen999/ps5-super-pldmgr-auto-updater" in source or "nexgen999/ps5-super-pldmgr-auto-updater" in origin:
        return True
    return False


def clear_icon(pkg: dict, reason: str) -> None:
    pkg_id = str(pkg.get("id") or "")
    path = ICONS / f"{pkg_id}.png"
    if path.exists():
        path.unlink()
    pkg["icon_url"] = ""
    pkg.pop("icon_source_url", None)
    pkg.pop("icon_origin", None)
    pkg.pop("icon_bytes", None)
    print(f"[icon:drop] {pkg_id}: {reason}")


def main() -> int:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    packages = catalog.get("packages") or []

    for pkg in packages:
        if untrusted(pkg):
            clear_icon(pkg, "generic/legacy source")

    # Duplicate image bytes from different apps are suspicious unless every member of
    # the group has a direct, app-specific upstream source. This catches future generic
    # site avatars/placeholders without hard-coding their digest.
    by_hash: dict[str, list[dict]] = defaultdict(list)
    for pkg in packages:
        pkg_id = str(pkg.get("id") or "")
        path = ICONS / f"{pkg_id}.png"
        if pkg.get("icon_url") and path.exists():
            by_hash[hashlib.sha256(path.read_bytes()).hexdigest()].append(pkg)

    for digest, group in by_hash.items():
        if len(group) < 2:
            continue
        if all(str(p.get("icon_origin") or "").lower() == "direct" for p in group):
            continue
        for pkg in group:
            clear_icon(pkg, f"duplicate artwork {digest[:12]}")

    valid = []
    total = 0
    for pkg in packages:
        pkg_id = str(pkg.get("id") or "")
        path = ICONS / f"{pkg_id}.png"
        if pkg.get("icon_url") and path.exists():
            valid.append(pkg)
            total += path.stat().st_size

    stats = catalog.setdefault("_stats", {})
    stats["icons"] = len(valid)
    stats["icon_bytes"] = total
    stats["icons_unresolved"] = len(packages) - len(valid)
    CATALOG.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Trusted icons: {len(valid)}/{len(packages)}; {total} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
