"""Run the full-granule tests, refusing absent data or skipped validation."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


def require_executed_tests(report, required_names):
    cases = ET.parse(report).findall(".//testcase")
    if not cases or any(case.find("skipped") is not None
                        or case.find("failure") is not None
                        or case.find("error") is not None for case in cases):
        raise SystemExit("full-granule validation requires passing executed tests with no skips")
    names = [case.get("name") for case in cases]
    if len(names) != len(required_names) or set(names) != set(required_names):
        raise SystemExit("full-granule validation did not execute the complete required test inventory")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junitxml", type=Path, default=Path("full-granules.xml"))
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    directory = os.environ.get("NCARNATE_GRANULE_DIR")
    if not directory:
        parser.error("set NCARNATE_GRANULE_DIR to the verified corpus directory")
    subprocess.run([sys.executable, str(repo / "tools/corpus.py"), directory,
                    "--include-references"], cwd=repo, check=True)
    report = args.junitxml.resolve()
    subprocess.run([sys.executable, "-m", "pytest", "-q", "-m", "raw_granules",
                    "tests/test_full_corpus.py", "tests/test_raw_granules.py",
                    "--junitxml", str(report)], cwd=repo, check=True)
    catalog = json.loads((repo / "tests/fixtures/corpus.json").read_text(encoding="utf-8"))
    required = {f"test_exact_full_granule[{entry['name']}]" for entry in catalog["granules"]}
    required.update({"test_amsre_conversion_matches_thg_reference", "test_mod03_decimation_oracle",
                     "test_myd05_fixture_regenerates_from_exact_source"})
    require_executed_tests(report, required)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
