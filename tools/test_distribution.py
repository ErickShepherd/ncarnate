"""Test a built artifact in a fresh environment outside the source checkout.

The source CI separately runs the distribution-content/build tests. Here the
runtime suite must import the installed package, with no source tree fallback.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import venv


def stage_tests(artifact, repo, root, kind):
    if kind == "wheel":
        shutil.copytree(repo / "tests", root / "tests", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        return
    # Exercise the tests/fixtures actually shipped in the source archive.
    # Copy regular files only, without following archive links or traversal.
    with tarfile.open(artifact, "r:gz") as archive:
        for member in archive.getmembers():
            parts = member.name.split("/", 1)
            if len(parts) != 2 or not parts[1].startswith("tests/"):
                continue
            relative = parts[1]
            target = root / relative
            if ("\\" in relative or ":" in relative or ".." in Path(relative).parts
                    or not target.resolve().is_relative_to((root / "tests").resolve())):
                raise ValueError("unsafe test path in source archive")
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError("source archive tests must contain only regular files")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--kind", choices=("wheel", "sdist"), required=True)
    args = parser.parse_args()
    pattern = "ncarnate-*.whl" if args.kind == "wheel" else "ncarnate-*.tar.gz"
    artifacts = list(args.directory.resolve().glob(pattern))
    if len(artifacts) != 1:
        parser.error(f"expected exactly one {args.kind}, found {len(artifacts)}")
    repo = Path(__file__).resolve().parents[1]
    environment = {key: value for key, value in os.environ.items()
                   if key not in {"PYTHONPATH", "PYTHONHOME"}}
    with tempfile.TemporaryDirectory(prefix="ncarnate-installed-") as directory:
        root = Path(directory)
        runtime = root / "venv"
        venv.EnvBuilder(with_pip=True).create(runtime)
        python = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        def run(*arguments):
            subprocess.run([str(python), *arguments], cwd=root, env=environment, check=True)
        run("-m", "pip", "install", str(artifacts[0]) + "[test]")
        run("-m", "pip", "check")
        stage_tests(artifacts[0], repo, root, args.kind)
        run("-c", "import ncarnate,sys; from pathlib import Path; "
            "assert Path(ncarnate.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()); "
            "print('Testing installed:', ncarnate.__version__, ncarnate.__file__)")
        (root / "pytest.ini").write_text(
            "[pytest]\nmarkers =\n    raw_granules: optional external full-size corpus\n",
            encoding="utf-8",
        )
        run("-m", "pytest", "-q", "tests", "--ignore=tests/test_sdist_contents.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
