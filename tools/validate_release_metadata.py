"""Check release metadata locally; never publish or contact external services.

Requires PyYAML and, on Python 3.10, tomli. --sdist also verifies the candidate
conda source digest. --require-release-date is a publication-time gate.
"""
import argparse
from datetime import date
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import re
import tarfile

import yaml
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


def validate(root, *, sdist=None, tag=None, require_release_date=False, require_tag=False):
    errors = []
    version = re.search(r'__version__\s*=\s*"([^"]+)"',
                        (root / 'ncarnate/constants.py').read_text(encoding="utf-8")).group(1)
    project = tomllib.loads((root / 'pyproject.toml').read_text(encoding="utf-8"))['project']
    citation = yaml.safe_load((root / 'CITATION.cff').read_text(encoding="utf-8"))
    zenodo = json.loads((root / '.zenodo.json').read_text(encoding="utf-8"))
    recipe = yaml.safe_load((root / 'conda-recipe/recipe.yaml').read_text(encoding="utf-8"))
    changelog = (root / 'CHANGELOG.md').read_text(encoding="utf-8")
    for name, found in [('citation', citation['version']), ('Zenodo', zenodo['version']),
                        ('conda', recipe['context']['version'])]:
        if found != version:
            errors.append(f'{name} version differs from package version {version}')
    if f'## [{version}] - ' not in changelog:
        errors.append('current version is missing from the changelog')
    if project['requires-python'] != '>=' + recipe['context']['python_min']:
        errors.append('conda Python floor differs from PyPI')
    def normalize(value):
        return re.sub(r'\s+', '', value).lower()
    conda_dependencies = {normalize(value) for value in recipe['requirements']['run']}
    for dependency in project['dependencies']:
        if normalize(dependency) not in conda_dependencies:
            errors.append(f'conda dependency differs from PyPI: {dependency}')
    for name, target in project['scripts'].items():
        if f'{name} = {target}' not in recipe['build']['python']['entry_points']:
            errors.append(f'conda entry point missing: {name}')
    concept = '10.5281/zenodo.21288802'
    if citation.get('doi') != concept or project['urls'].get('Citation') != 'https://doi.org/' + concept:
        errors.append('citation must retain the verified concept DOI')
    if zenodo.get('doi'):
        errors.append('do not reuse a version-specific DOI in Zenodo upload metadata')
    if tag and tag != 'v' + version:
        errors.append('release tag differs from the package version')
    if require_tag and not tag:
        errors.append('production publication requires a version tag, not an untagged branch')
    if require_release_date:
        released = citation.get('date-released')
        try:
            parsed = date.fromisoformat(str(released))
            if parsed > date.today():
                errors.append('release date is in the future')
        except ValueError:
            errors.append('set the actual citation release date before publication')
        if not released or f'## [{version}] - {released}' not in changelog:
            errors.append('date the changelog entry to match the citation before publication')
    if sdist is not None:
        digest = hashlib.sha256(sdist.read_bytes()).hexdigest()
        if digest != recipe['source']['sha256']:
            errors.append('conda digest differs from the candidate source archive')
        with tarfile.open(sdist, 'r:gz') as archive:
            metadata = archive.extractfile(f'ncarnate-{version}/PKG-INFO')
            if metadata is None or BytesParser().parsebytes(metadata.read())['Version'] != version:
                errors.append('source archive version differs from package metadata')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdist', type=Path)
    parser.add_argument('--tag', default=os.environ.get('NCARNATE_RELEASE_TAG'))
    parser.add_argument('--require-release-date', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    errors = validate(root, sdist=args.sdist, tag=args.tag,
                      require_tag=os.environ.get('NCARNATE_PUBLISH_TARGET') == 'pypi',
                      require_release_date=args.require_release_date or bool(args.tag)
                      or os.environ.get('NCARNATE_PUBLISH_TARGET') == 'pypi')
    if errors:
        for error in errors:
            print('release metadata: ' + error)
        return 1
    print('Release metadata is consistent' + (' with the candidate source archive' if args.sdist else ''))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
