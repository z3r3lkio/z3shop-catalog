#!/usr/bin/env python3
import html
import json
import os
import re
import subprocess
import sys
import urllib.request
from collections import Counter
from pathlib import PurePosixPath

FOLDER_ID = "1PUxUy5sHsyonAzSjA_OcW__4i58LgdfP"
FOLDER_URL = f"https://drive.google.com/drive/folders/{FOLDER_ID}"
SOURCE_ID = f"gdrive-{FOLDER_ID}"
OUTPUT = "sources/google-drive-direct.json"
USER_AGENT = "Mozilla/5.0 Z3Shop-Catalog/1.0"


def classify(path: str) -> str:
    lower = path.lower()
    for suffix, kind in (
        (".ffpfsc", "ffpfsc"),
        (".ffpkg", "ffpkg"),
        (".pkg", "pkg"),
        (".lz4", "lz4"),
        (".exfat", "exfat"),
        (".zip", "archive"),
        (".7z", "archive"),
        (".rar", "archive"),
    ):
        if lower.endswith(suffix):
            return kind
    return "other"


def inventory(entries):
    files = []
    kinds = Counter()
    extensions = Counter()
    for entry in entries:
        path = str(entry.get("path") or entry.get("name") or "").replace("\\", "/").strip()
        if not path:
            continue
        name = PurePosixPath(path).name
        ext = PurePosixPath(name).suffix.lower() or "(none)"
        kind = classify(path)
        kinds[kind] += 1
        extensions[ext] += 1
        files.append({
            "path": path,
            "name": name,
            "extension": ext,
            "kind": kind,
        })
    return files, kinds, extensions


def list_with_gdown():
    cmd = [sys.executable, "-m", "gdown", FOLDER_URL, "--folder", "--json", "--quiet"]
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=120)
    if proc.returncode != 0:
        return None, (proc.stderr.strip() or f"gdown exit {proc.returncode}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return None, f"invalid gdown JSON: {exc}"
    return data, ""


def fetch_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.status, response.read().decode("utf-8", errors="replace")


def list_with_embedded_view():
    urls = [
        f"https://drive.google.com/embeddedfolderview?id={FOLDER_ID}#list",
        f"{FOLDER_URL}?usp=sharing",
    ]
    errors = []
    for url in urls:
        try:
            status, body = fetch_text(url)
        except Exception as exc:
            errors.append(f"{url}: {exc}")
            continue
        if status != 200:
            errors.append(f"{url}: HTTP {status}")
            continue

        matches = re.findall(r'data-id="([^"]+)"[^>]*?(?:data-tooltip|aria-label)="([^"]+)"', body, flags=re.I | re.S)
        if not matches:
            matches = re.findall(r'(?:data-tooltip|aria-label)="([^"]+)"[^>]*?data-id="([^"]+)"', body, flags=re.I | re.S)
            matches = [(file_id, name) for name, file_id in matches]

        entries = []
        seen = set()
        for file_id, raw_name in matches:
            name = html.unescape(raw_name).strip()
            if not file_id or not name or file_id in seen:
                continue
            seen.add(file_id)
            entries.append({"name": name})
        if entries:
            return entries, ""

        if "accounts.google.com" in body or "Sign in" in body or "request access" in body.lower():
            errors.append(f"{url}: authentication required")
        else:
            errors.append(f"{url}: no public file entries found")
    return None, "; ".join(errors)


def write_manifest(files, kinds, extensions, status, detail, method):
    payload = {
        "source": {
            "id": SOURCE_ID,
            "type": "google-drive-folder",
            "folder_url": FOLDER_URL,
            "mode": "direct-source-inventory",
            "access_status": status,
            "inventory_method": method,
            "distribution_status": "unverified",
            "note": "Inventory only until redistribution/install rights are verified. File IDs and direct download URLs are intentionally not mirrored here.",
        },
        "count": len(files),
        "kind_counts": dict(sorted(kinds.items())),
        "extension_counts": dict(sorted(extensions.items(), key=lambda kv: (-kv[1], kv[0]))),
        "files": files,
    }
    if detail:
        payload["source"]["access_detail"] = detail[:1000]
    with open(OUTPUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def main() -> int:
    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)

    entries, gdown_error = list_with_gdown()
    method = "gdown-json"
    detail = ""
    if entries is None:
        print(f"[gdrive] gdown unavailable: {gdown_error}")
        entries, fallback_error = list_with_embedded_view()
        method = "embedded-folder-view"
        detail = f"gdown: {gdown_error}; fallback: {fallback_error}".strip("; ")

    if entries is None:
        files, kinds, extensions = [], Counter(), Counter()
        write_manifest(files, kinds, extensions, "inaccessible", detail, method)
        print("[gdrive] source registered but anonymous inventory is currently inaccessible")
        return 0

    files, kinds, extensions = inventory(entries)
    write_manifest(files, kinds, extensions, "public", detail, method)
    print(f"[gdrive] Google Drive source: {len(files)} files via {method}")
    print("[gdrive] Kinds:", dict(kinds))
    for item in files[:80]:
        print(f" - [{item['kind']}] {item['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
