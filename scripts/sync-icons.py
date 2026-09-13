#!/usr/bin/env python3
"""Resolve true upstream artwork, optimize it and self-host compact PNGs for Z3Shop."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "packages.json"
ICONS_DIR = ROOT / "icons"
RAW_BASE = "https://raw.githubusercontent.com/z3r3lkio/z3shop-catalog/main/icons"
TOKEN = os.environ.get("GITHUB_TOKEN", "")
USER_AGENT = "Z3Shop-Catalog-IconSync/1.1"
MAX_DOWNLOAD = 8 * 1024 * 1024
ICON_SIDE = 256
TARGET_MAX_BYTES = 120 * 1024

# Aggregators are valid metadata/download sources but are not valid artwork sources for
# an individual app. Using their repository icon would make many unrelated apps share
# the same image, which is worse than leaving the icon unresolved.
AGGREGATOR_REPOS = {
    "nexgen999/ps5-super-pldmgr-auto-updater",
    "z3r3lkio/z3shop-catalog",
}
AGGREGATOR_URL_PARTS = (
    "/nexgen999/PS5-Super-PLDMGR-Auto-Updater/",
    "/z3r3lkio/z3shop-catalog/",
)

DIRECT_SOURCES = {
    "ps5-homebrew-launcher": "https://raw.githubusercontent.com/ps5-payload-dev/websrv/master/icon0.png",
    "prospero-light": "https://raw.githubusercontent.com/blackbearreloaded/ProsperoLight/main/sce_sys/icon0.png",
    "prospero-radio": "https://raw.githubusercontent.com/blackbearreloaded/ProsperoRadio/main/sce_sys/icon0.png",
    "prospero-tv": "https://raw.githubusercontent.com/blackbearreloaded/ProsperoTV/main/sce_sys/icon0.png",
    "itemzflow-game-manager": "https://raw.githubusercontent.com/LightningMods/Itemzflow/main/App-Media-Assets/sce_sys/icon0.png",
    "fpkgi": "https://raw.githubusercontent.com/ItsJokerZz/FPKGi/release/source/Assets/Images/App/Icon.png",
    "ps5-shop-appkg": "https://raw.githubusercontent.com/ps5xploit/ps5shopappkg/main/PS5SHOPAPPKG-icon0.jpg",
}

UPSTREAM_REPOS = {
    "ps5-homebrew-launcher": "ps5-payload-dev/websrv",
    "prospero-light": "blackbearreloaded/ProsperoLight",
    "prospero-radio": "blackbearreloaded/ProsperoRadio",
    "prospero-tv": "blackbearreloaded/ProsperoTV",
    "itemzflow-game-manager": "LightningMods/Itemzflow",
    "fpkgi": "ItsJokerZz/FPKGi",
    "ps5-shop-appkg": "ps5xploit/ps5shopappkg",
}

# When source code is not public, use the app's own PKG-Zone detail page as the next
# authoritative source. The resolver extracts the page's app image dynamically.
DETAIL_PAGES = {
    "ps5-xplorer": "https://pkg-zone.com/details/LAPY20011",
    "avatar-changer": "https://pkg-zone.com/details/LAPY20016",
    "fpkgi": "https://pkg-zone.com/details/PKGI13337",
    "itemzflow-game-manager": "https://pkg-zone.com/details/ITEM00001",
}


def headers(url: str, accept: str = "*/*") -> dict[str, str]:
    h = {"User-Agent": USER_AGENT, "Accept": accept}
    if TOKEN and url.startswith("https://api.github.com/"):
        h["Authorization"] = f"Bearer {TOKEN}"
        h["X-GitHub-Api-Version"] = "2022-11-28"
    return h


def fetch_bytes(url: str, *, max_bytes: int = MAX_DOWNLOAD, accept: str = "*/*") -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers=headers(url, accept))
    with urllib.request.urlopen(req, timeout=20) as response:
        length = response.headers.get("Content-Length")
        if length and int(length) > max_bytes:
            raise ValueError(f"source too large: {length} bytes")
        data = response.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError(f"source exceeded {max_bytes} bytes")
        return data, response.geturl()


def fetch_json(url: str):
    data, _ = fetch_bytes(url, accept="application/vnd.github+json, application/json")
    return json.loads(data.decode("utf-8"))


def repo_from_homepage(homepage: str) -> str | None:
    try:
        parsed = urllib.parse.urlparse(homepage)
    except Exception:
        return None
    if parsed.netloc.lower() != "github.com":
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        return None
    repo = f"{parts[0]}/{parts[1].removesuffix('.git')}"
    return None if repo.lower() in AGGREGATOR_REPOS else repo


def image_score(path: str, size: int) -> int:
    lower = path.lower()
    name = lower.rsplit("/", 1)[-1]
    if not lower.endswith((".png", ".jpg", ".jpeg", ".webp")) or size <= 0 or size > MAX_DOWNLOAD:
        return -10_000
    score = 0
    if lower.endswith("/sce_sys/icon0.png") or lower == "sce_sys/icon0.png":
        score += 1000
    if name == "icon0.png":
        score += 900
    elif name in {"icon.png", "appicon.png", "app-icon.png"}:
        score += 750
    elif "icon" in name:
        score += 500
    elif "logo" in name:
        score += 350
    if "/images/app/" in lower or "/app-media-assets/" in lower:
        score += 180
    if any(bad in lower for bad in ("background", "screenshot", "gallery", "banner", "cover", "pic0", "pic1")):
        score -= 500
    if size < 1_000_000:
        score += 40
    return score


def discover_repo_icon(repo: str) -> str | None:
    if repo.lower() in AGGREGATOR_REPOS:
        return None
    meta = fetch_json(f"https://api.github.com/repos/{repo}")
    branch = meta.get("default_branch") or "main"
    tree = fetch_json(f"https://api.github.com/repos/{repo}/git/trees/{urllib.parse.quote(branch, safe='')}?recursive=1")
    candidates: list[tuple[int, str]] = []
    for item in tree.get("tree", []):
        if item.get("type") != "blob":
            continue
        path = str(item.get("path") or "")
        score = image_score(path, int(item.get("size") or 0))
        if score > 0:
            candidates.append((score, path))
    if not candidates:
        return None
    candidates.sort(key=lambda x: (-x[0], len(x[1]), x[1].lower()))
    path = candidates[0][1]
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{urllib.parse.quote(path, safe='/')}"


def detail_page_icon(url: str) -> str | None:
    data, final_url = fetch_bytes(url, max_bytes=2 * 1024 * 1024, accept="text/html,application/xhtml+xml")
    html = data.decode("utf-8", errors="replace")
    patterns = [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image(?::src)?["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.I)
        if match:
            candidate = urllib.parse.urljoin(final_url, match.group(1).replace("&amp;", "&"))
            if candidate.startswith("https://"):
                return candidate

    # Fallback for pages without social metadata. Prefer image URLs near app/detail markup
    # and reject obvious site chrome.
    ranked: list[tuple[int, str]] = []
    for match in re.finditer(r'<img([^>]+)src=["\']([^"\']+)["\']([^>]*)>', html, flags=re.I):
        attrs = (match.group(1) + " " + match.group(3)).lower()
        src = urllib.parse.urljoin(final_url, match.group(2).replace("&amp;", "&"))
        low = src.lower()
        if not src.startswith("https://") or not any(ext in low for ext in (".png", ".jpg", ".jpeg", ".webp")):
            continue
        if any(skip in low for skip in ("logo", "discord", "github", "banner")):
            continue
        score = 20
        if any(word in attrs for word in ("app", "cover", "package", "detail", "card")):
            score += 40
        if any(word in low for word in ("app", "cover", "package", "icon")):
            score += 30
        ranked.append((score, src))
    if ranked:
        ranked.sort(key=lambda x: -x[0])
        return ranked[0][1]
    return None


def trusted_catalog_source(value: str) -> bool:
    if not value.startswith("https://") or value.startswith(RAW_BASE + "/"):
        return False
    return not any(part.lower() in value.lower() for part in AGGREGATOR_URL_PARTS)


def candidate_sources(pkg: dict) -> list[tuple[str, str]]:
    pkg_id = str(pkg.get("id") or "")
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(origin: str, value: str | None):
        if not value or value in seen or not value.startswith("https://"):
            return
        if value.startswith(RAW_BASE + "/"):
            return
        seen.add(value)
        candidates.append((origin, value))

    # 1) exact upstream source; 2) authoritative detail page; 3) trusted previous source;
    # 4) source-repository discovery. Aggregator repositories are deliberately excluded.
    add("direct", DIRECT_SOURCES.get(pkg_id))

    page = DETAIL_PAGES.get(pkg_id)
    if page:
        try:
            add(f"page:{page}", detail_page_icon(page))
        except Exception as exc:
            print(f"[icon:page-warn] {pkg_id}: {page}: {exc}")

    for value in (str(pkg.get("icon_source_url") or ""), str(pkg.get("icon_url") or "")):
        if trusted_catalog_source(value):
            add("catalog-upstream", value)

    repo = UPSTREAM_REPOS.get(pkg_id) or repo_from_homepage(str(pkg.get("homepage") or ""))
    if repo:
        try:
            add(f"github:{repo}", discover_repo_icon(repo))
        except Exception as exc:
            print(f"[icon:repo-warn] {pkg_id}: {repo}: {exc}")
    return candidates


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


def git_head_catalog() -> dict | None:
    try:
        text = subprocess.check_output(["git", "show", "HEAD:packages.json"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL)
        return json.loads(text)
    except Exception:
        return None


def comparable(catalog: dict) -> str:
    clone = json.loads(json.dumps(catalog))
    clone.pop("_generated", None)
    return json.dumps(clone, sort_keys=True, separators=(",", ":"))


def bad_cached_source(pkg: dict) -> bool:
    origin = str(pkg.get("icon_origin") or "").lower()
    source = str(pkg.get("icon_source_url") or "").lower()
    return "nexgen999/ps5-super-pldmgr-auto-updater" in origin or "/nexgen999/ps5-super-pldmgr-auto-updater/" in source


def main() -> int:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    packages = catalog.get("packages")
    if not isinstance(packages, list):
        raise SystemExit("packages.json has no packages array")

    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    active_files: set[str] = set()
    resolved = 0
    total_saved = 0

    for pkg in packages:
        pkg_id = str(pkg.get("id") or "").strip()
        if not pkg_id:
            continue
        filename = f"{pkg_id}.png"
        dest = ICONS_DIR / filename
        active_files.add(filename)
        source_used = ""
        origin_used = ""
        last_error = ""

        for origin, source_url in candidate_sources(pkg):
            try:
                raw, final_url = fetch_bytes(source_url, accept="image/*,*/*;q=0.8")
                data = optimized_png(raw)
                if len(data) > 220 * 1024:
                    raise ValueError(f"optimized icon still too large: {len(data)} bytes")
                if not dest.exists() or dest.read_bytes() != data:
                    dest.write_bytes(data)
                source_used = final_url
                origin_used = origin
                break
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"

        if not source_used and dest.exists() and not bad_cached_source(pkg):
            # Preserve a previously verified icon across a transient upstream outage.
            source_used = str(pkg.get("icon_source_url") or "cached")
            origin_used = "cached"

        if source_used and dest.exists():
            digest = hashlib.sha256(dest.read_bytes()).hexdigest()
            pkg["icon_source_url"] = source_used
            pkg["icon_origin"] = origin_used
            pkg["icon_url"] = f"{RAW_BASE}/{filename}?v={digest[:12]}"
            pkg["icon_bytes"] = dest.stat().st_size
            resolved += 1
            total_saved += dest.stat().st_size
            print(f"[icon:ok] {pkg_id}: {dest.stat().st_size} bytes <- {origin_used}")
        else:
            if dest.exists():
                dest.unlink()
            pkg["icon_url"] = ""
            pkg.pop("icon_source_url", None)
            pkg.pop("icon_origin", None)
            pkg.pop("icon_bytes", None)
            print(f"[icon:missing] {pkg_id}: {last_error or 'no trustworthy upstream artwork found'}")

    for orphan in ICONS_DIR.glob("*.png"):
        if orphan.name not in active_files:
            orphan.unlink()
            print(f"[icon:prune] {orphan.name}")

    catalog.setdefault("_stats", {})["icons"] = resolved
    catalog["_stats"]["icon_bytes"] = total_saved

    previous = git_head_catalog()
    if previous and comparable(catalog) == comparable(previous):
        catalog["_generated"] = previous.get("_generated", catalog.get("_generated"))

    CATALOG_PATH.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Resolved {resolved}/{len(packages)} trustworthy icons; optimized payload {total_saved} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
