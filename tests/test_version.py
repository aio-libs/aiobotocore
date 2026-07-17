from pathlib import Path

from packaging import version

import aiobotocore
from scripts.changelog import parse, validate

_root_path = Path(__file__).absolute().parent.parent


def test_release_versions():
    # Cross-checks the top entry of CHANGES.rst against
    # aiobotocore/__init__.py and the entry below it. The CHANGES.rst
    # parser + format invariants live in scripts/changelog.py so the
    # auto-release workflow can use the same logic without installing
    # the project's dev deps.
    init_version = version.parse(aiobotocore.__version__)
    # init version should be in canonical form
    assert str(init_version) == aiobotocore.__version__

    changes_text = (_root_path / 'CHANGES.rst').read_text(encoding='utf-8')

    # Format invariants: top entry version matches __init__.py, top
    # entry's version > previous entry's version, dates are non-
    # increasing (or TBD).
    validate(changes_text, expected_top_version=aiobotocore.__version__)

    # Stronger version-ordering check using packaging's PEP 440 parser
    # (catches edge cases like 1.2.3rc1 vs 1.2.3 that the simple tuple
    # comparison in scripts/changelog.py treats differently).
    entries = parse(changes_text)
    assert len(entries) >= 2, 'CHANGES.rst should have at least two entries'
    top, prev = entries[0], entries[1]
    assert version.parse(top.version) > version.parse(prev.version), (
        f'top entry {top.version} should be > previous {prev.version}'
    )
