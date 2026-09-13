#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import PurePosixPath

FOLDER_URL = "https://drive.google.com/drive/folders/1PUxUy5sHsyonAzSjA_OcW__4i58LgdfP"
SOURCE_ID = "gdrive-1PUxUy5sHsyonAzSjA_OcW__4i58LgdfP"
OUTPUT = "sources/google-drive-direct.json"


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


def main() -> int:
    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    cmd = [sys.executable, "-m", "gdown", FOLDER_URL, "--folder", "--json", "--quiet"]
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=120)
    if proc.returncode != 0:
        print(proc.stderr.strip() or "gdown failed", file=sys.stderr)
        return proc.returncode or 1

    try:
        entries = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        print(f"invalid gdown JSON: {exc}", file=sys.stderr)
        print(proc.stdout[:2000], file=sys.stderr)
        return 2

    files = []
    kinds = Counter()
    extensions = Counter()
    for entry in entries:
        path = str(entry.get("path") or "").replace("\\", "/").strip()
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

    payload = {
        "source": {
            "id": SOURCE_ID,
            "type": "google-drive-folder",
            "folder_url": FOLDER_URL,
            "mode": "direct-source-inventory",
            "distribution_status": "unverified",
            "note": "Inventory only until redistribution/install rights are verified. File IDs and direct download URLs are intentionally not mirrored here.",
        },
        "count": len(files),
        "kind_counts": dict(sorted(kinds.items())),
        "extension_counts": dict(sorted(extensions.items(), key=lambda kv: (-kv[1], kv[0]))),
        "files": files,
    }
    with open(OUTPUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print(f"Google Drive source: {len(files)} files")
    print("Kinds:", dict(kinds))
    for item in files[:80]:
        print(f" - [{item['kind']}] {item['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
