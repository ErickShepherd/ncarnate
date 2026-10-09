"""Fetch or verify exact original granules, never accepting a name as identity.

python tools/corpus.py DIRECTORY [--fetch]
Missing sources make the command fail. Downloads stream to a temporary file,
have a 512 MiB cap, and enter the corpus only after the pinned SHA-256 matches.
"""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.request


MAX_ATTEMPTS = 3
SOCKET_TIMEOUT = 30
TRANSFER_SECONDS = 120
MAX_BYTES = 512 * 1024 * 1024


def transient(error):
    """Retry temporary transport failures, never bad bytes or local I/O errors."""
    if isinstance(error, urllib.error.HTTPError):
        return error.code in {408, 429, 500, 502, 503, 504}
    if isinstance(error, urllib.error.URLError):
        error = error.reason
    if isinstance(error, socket.gaierror):
        return error.errno == socket.EAI_AGAIN
    return isinstance(error, (TimeoutError, ConnectionError, http.client.IncompleteRead))


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
            for attempt in range(1, MAX_ATTEMPTS + 1):
                temporary = None
                retry = False
                try:
                    if not url.startswith("https://"):
                        raise ValueError("download URL must use HTTPS")
                    deadline = time.monotonic() + TRANSFER_SECONDS
                    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
                        temporary = Path(stream.name)
                        with urllib.request.urlopen(url, timeout=SOCKET_TIMEOUT) as response:
                            if not response.geturl().startswith("https://"):
                                raise ValueError("download redirected away from HTTPS")
                            total = 0
                            while True:
                                # read1 performs at most one underlying read, so
                                # a trickling server cannot bypass the deadline.
                                block = response.read1(1024 * 1024)
                                if time.monotonic() >= deadline:
                                    raise TimeoutError("download exceeded transfer time budget")
                                if not block:
                                    break
                                total += len(block)
                                if total > MAX_BYTES:
                                    raise ValueError("download exceeds 512 MiB limit")
                                stream.write(block)
                    if digest(temporary) != expected:
                        raise ValueError("download digest does not match exact recovered source")
                    # Never expose a partial copy or overwrite an existing file.
                    os.link(temporary, target)
                    return {"name": name, "status": "verified", "url": url}
                except (OSError, ValueError, http.client.IncompleteRead) as error:
                    if isinstance(error, urllib.error.HTTPError):
                        error.close()
                    message = f"{name}: attempt {attempt}/{MAX_ATTEMPTS}: {url}: {error}"
                    errors.append(message)
                    print(message, file=sys.stderr, flush=True)
                    retry = transient(error) and attempt < MAX_ATTEMPTS
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
                if not retry:
                    break
                time.sleep(2 ** attempt)
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
    for row in rows:
        print(f"{row['name']}: {row['status']}", file=sys.stderr)
    return 0 if all(row["status"] == "verified" for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
