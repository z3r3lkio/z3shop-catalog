#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
GAMES_DIR = ROOT / "games"
ART_DIR = ROOT / "game-art"
RAW_BASE = "https://raw.githubusercontent.com/z3r3lkio/z3shop-catalog/main/game-art"
USER_AGENT = "Z3Shop-Game-Art/1.0"
TARGET = 256
FOREGROUND_MAX = (224, 240)
MAX_FILE_BYTES = 225_280
WORKERS = 8


def safe_id(value: str) -> str:
    out = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-")
    return out[:64] or "game"


def art_name(game: dict) -> str | None:
    poster = str(game.get("poster_url") or "").strip()
    gid = str(game.get("id") or "").strip()
    if not poster or not gid:
        return None
    digest = hashlib.sha256(poster.encode("utf-8")).hexdigest()[:12]
    return f"{safe_id(gid)}-{digest}.png"


def download(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        if getattr(response, "status", 200) != 200:
            raise RuntimeError(f"HTTP {getattr(response, 'status', 'unknown')}")
        data = response.read(8 * 1024 * 1024 + 1)
    if not data or len(data) > 8 * 1024 * 1024:
        raise RuntimeError("empty or oversized source image")
    return data


def render_square(data: bytes, destination: Path) -> int:
    with Image.open(io.BytesIO(data)) as source:
        source = ImageOps.exif_transpose(source).convert("RGB")
        source.thumbnail(FOREGROUND_MAX, Image.Resampling.LANCZOS)

        canvas = Image.new("RGB", (TARGET, TARGET), (5, 15, 29))
        x = (TARGET - source.width) // 2
        y = (TARGET - source.height) // 2
        canvas.paste(source, (x, y))

        # A subtle frame makes portrait artwork look intentional inside the existing
        # square Z3Shop card without cropping the original cover.
        px = canvas.load()
        left = max(0, x - 2)
        top = max(0, y - 2)
        right = min(TARGET - 1, x + source.width + 1)
        bottom = min(TARGET - 1, y + source.height + 1)
        frame = (41, 119, 203)
        for xx in range(left, right + 1):
            px[xx, top] = frame
            px[xx, bottom] = frame
        for yy in range(top, bottom + 1):
            px[left, yy] = frame
            px[right, yy] = frame

        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".png", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            canvas.save(tmp_path, format="PNG", optimize=True, compress_level=9)
            if tmp_path.stat().st_size > MAX_FILE_BYTES:
                # Reduce dimensions only when the source is unusually noisy. The renderer
                # already scales icons, so this is preferable to large network stalls.
                compact = canvas.resize((192, 192), Image.Resampling.LANCZOS)
                compact.save(tmp_path, format="PNG", optimize=True, compress_level=9)
            if tmp_path.stat().st_size > MAX_FILE_BYTES:
                raise RuntimeError(f"optimized image still too large: {tmp_path.stat().st_size} bytes")
            os.replace(tmp_path, destination)
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
    return destination.stat().st_size


def load_pages() -> list[tuple[Path, dict]]:
    pages: list[tuple[Path, dict]] = []
    for path in sorted(GAMES_DIR.glob("page-*.json")):
        pages.append((path, json.loads(path.read_text(encoding="utf-8"))))
    if not pages:
        raise RuntimeError("no generated games pages found")
    return pages


def main() -> int:
    pages = load_pages()
    ART_DIR.mkdir(parents=True, exist_ok=True)

    work: dict[str, tuple[str, Path]] = {}
    active_names: set[str] = set()
    games_total = 0

    for _, page in pages:
        for game in page.get("games", []):
            games_total += 1
            name = art_name(game)
            if not name:
                game.pop("icon_url", None)
                continue
            active_names.add(name)
            destination = ART_DIR / name
            work[name] = (str(game.get("poster_url") or ""), destination)

    results: dict[str, tuple[bool, int, str]] = {}

    def sync_one(name: str, url: str, destination: Path):
        if destination.exists() and 0 < destination.stat().st_size <= MAX_FILE_BYTES:
            return name, True, destination.stat().st_size, "cached"
        try:
            data = download(url)
            size = render_square(data, destination)
            return name, True, size, "downloaded"
        except Exception as exc:  # noqa: BLE001 - report per-title and keep catalog usable
            destination.unlink(missing_ok=True)
            return name, False, 0, str(exc)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(sync_one, name, url, destination) for name, (url, destination) in work.items()]
        for future in as_completed(futures):
            name, ok, size, note = future.result()
            results[name] = (ok, size, note)
            if not ok:
                print(f"[art:warn] {name}: {note}", file=sys.stderr)

    # Remove stale hashed artwork when an upstream poster changes or a title disappears.
    for path in ART_DIR.glob("*.png"):
        if path.name not in active_names:
            path.unlink(missing_ok=True)

    ready = 0
    bytes_total = 0
    for path, page in pages:
        for game in page.get("games", []):
            name = art_name(game)
            result = results.get(name or "")
            if name and result and result[0]:
                game["icon_url"] = f"{RAW_BASE}/{name}"
                game["icon_bytes"] = result[1]
                ready += 1
                bytes_total += result[1]
            else:
                game.pop("icon_url", None)
                game.pop("icon_bytes", None)
        path.write_text(json.dumps(page, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    index_path = GAMES_DIR / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["artwork"] = {
        "mode": "optimized-local-thumbnails",
        "directory": "game-art",
        "target_px": TARGET,
        "ready": ready,
        "unresolved": max(0, games_total - ready),
        "bytes": bytes_total,
    }
    index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Game artwork ready: {ready}/{games_total}; {bytes_total} bytes total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
