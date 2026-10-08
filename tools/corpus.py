"""Fetch or verify exact original granules, never accepting a name as identity.

python tools/corpus.py DIRECTORY [--fetch]
Missing sources make the command fail. Downloads stream to a temporary file,
have a 512 MiB cap, and enter the corpus only after the pinned SHA-256 matches.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import urllib.request


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


def acquire(entry, directory, *, fetch=False):
    name, expected = entry["name"], entry["sha256"]
    if Path(name).name != name or "/" in name or "\\" in name or ":" in name:
        raise ValueError("corpus name must be a plain filename")
    if not re.fullmatch("[0-9a-f]{64}", expected):
        raise ValueError("invalid pinned digest")
    target = directory / name
    if target.is_symlink():
        raise ValueError("corpus target is a symlink")
    if target.exists():
        return {"name": name, "status": "verified" if digest(target) == expected else "digest_mismatch"}
    errors = []
    if fetch:
        directory.mkdir(parents=True, exist_ok=True)
        for url in entry.get("urls", []):
            temporary = None
            try:
                if not url.startswith("https://"):
                    raise ValueError("download URL must use HTTPS")
                with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
                    temporary = Path(stream.name)
                    with urllib.request.urlopen(url, timeout=30) as response:
                        if not response.geturl().startswith("https://"):
                            raise ValueError("download redirected away from HTTPS")
                        total = 0
                        while block := response.read(1024 * 1024):
                            total += len(block)
                            if total > 512 * 1024 * 1024:
                                raise ValueError("download exceeds 512 MiB limit")
                            stream.write(block)
                if digest(temporary) != expected:
                    raise ValueError("download digest does not match exact recovered source")
                # Atomic exclusive publication on this same filesystem; never
                # expose a partial copy or overwrite a concurrently supplied file.
                os.link(temporary, target)
                return {"name": name, "status": "verified", "url": url}
            except (OSError, ValueError) as error:
                errors.append(f"{url}: {error}")
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
    return {"name": name, "status": "missing", "attempts": errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--include-references", action="store_true", help="also verify/fetch independent reference products")
    args = parser.parse_args()
    catalog = Path(__file__).resolve().parents[1] / "tests/fixtures/corpus.json"
    manifest = json.loads(catalog.read_text())
    entries = manifest["granules"] + (manifest.get("references", []) if args.include_references else [])
    rows = [acquire(entry, args.directory, fetch=args.fetch) for entry in entries]
    print(json.dumps(rows, indent=2))
    return 0 if all(row["status"] == "verified" for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
