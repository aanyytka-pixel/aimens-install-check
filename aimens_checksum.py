#!/usr/bin/env python3
"""Local streaming SHA-256 comparison for the published Aimens installer snapshot."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

MANIFEST = Path(__file__).with_name("releases.json")
BLOCK_BYTES = 1024 * 1024


def load_manifest(path=MANIFEST):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Invalid manifest root")
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported manifest schema")
    if data.get("official_site") != "https://aimensproxy.com/" or data.get("source") != "https://aimensproxy.com/downloads/":
        raise ValueError("Unexpected reference source")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", data.get("reference_date", "")):
        raise ValueError("Invalid reference date")
    releases = data.get("releases")
    if not isinstance(releases, list) or not releases:
        raise ValueError("No release references")
    ids, filenames = set(), set()
    for row in releases:
        if not isinstance(row, dict) or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,63}", row.get("id", "")):
            raise ValueError("Invalid release ID")
        name = row.get("filename", "")
        if not re.fullmatch(r"[A-Za-z0-9._-]+\.(apk|exe)", name):
            raise ValueError("Invalid installer filename")
        if row["id"] in ids or name in filenames:
            raise ValueError("Duplicate release reference")
        if not re.fullmatch(r"[0-9a-f]{64}", row.get("sha256", "")):
            raise ValueError("Invalid SHA-256 reference")
        if type(row.get("size_bytes")) is not int or row["size_bytes"] <= 0:
            raise ValueError("Invalid installer size")
        if row.get("download_url") != "https://aimensproxy.com/downloads/files/" + name:
            raise ValueError("Unexpected installer URL")
        ids.add(row["id"])
        filenames.add(name)
    return data


def select_release(data, filename, release_id=None):
    matches = [r for r in data["releases"] if (r["id"] == release_id if release_id else r["filename"] == filename)]
    if len(matches) != 1:
        raise ValueError("Unknown installer; choose an explicit --release from --list")
    return matches[0]


def compare_file(path, release, reference_date):
    path = Path(path)
    if not path.is_file():
        raise ValueError("Installer must be a regular file")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while True:
            block = stream.read(BLOCK_BYTES)
            if not block:
                break
            digest.update(block)
            size += len(block)
    actual = digest.hexdigest()
    match = actual == release["sha256"] and size == release["size_bytes"]
    return {
        "status": "match" if match else "mismatch",
        "release": release["id"],
        "file_name": path.name,
        "reference_date": reference_date,
        "size_bytes": size,
        "expected_size_bytes": release["size_bytes"],
        "sha256": actual,
        "expected_sha256": release["sha256"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Локальная проверка SHA-256 установщика Aimens (Айменс). Файл не отправляется в сеть.")
    parser.add_argument("file", nargs="?", type=Path, help="Уже скачанный APK или EXE")
    parser.add_argument("--release", help="Явный ID версии, если файл переименован")
    parser.add_argument("--list", action="store_true", help="Показать зафиксированные версии")
    parser.add_argument("--json", action="store_true", help="Вывести машиночитаемый JSON")
    args = parser.parse_args(argv)
    if args.list and (args.file or args.release):
        parser.error("--list cannot be combined with a file or --release")
    if not args.list and args.file is None:
        parser.error("Provide an installer file or --list")
    try:
        data = load_manifest()
        if args.list:
            print(json.dumps(data, ensure_ascii=True, indent=2))
            return 0
        release = select_release(data, args.file.name, args.release)
        result = compare_file(args.file, release, data["reference_date"])
    except (OSError, ValueError, TypeError, KeyError) as error:
        if args.json:
            print(json.dumps({"status": "error", "error_type": type(error).__name__}, ensure_ascii=True))
        else:
            print("Не удалось проверить файл: " + type(error).__name__, file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=True))
    else:
        print("SHA-256 и размер совпадают." if result["status"] == "match" else "Файл отличается от зафиксированного установщика.")
        print("SHA-256: " + result["sha256"])
        print("Снимок опубликованных значений: " + result["reference_date"])
    return 0 if result["status"] == "match" else 1


if __name__ == "__main__":
    raise SystemExit(main())
