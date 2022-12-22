import re
from datetime import datetime
from pathlib import Path

import docutils.frontend
import docutils.nodes
import docutils.parsers.rst
import docutils.utils
import pytest
from packaging import version

import aiobotocore

_root_path = Path(__file__).absolute().parent.parent


# date can be YYYY-MM-DD or "TBD"
_rst_ver_date_str_re = re.compile(
    r'(?P<version>\d+\.\d+\.\d+) \((?P<date>\d{4}-\d{2}-\d{2}|TBD)\)'
)


# from: https://stackoverflow.com/a/48719723/1241593
def _parse_rst(text: str) -> docutils.nodes.document:
    parser = docutils.parsers.rst.Parser()
    components = (docutils.parsers.rst.Parser,)
    settings = docutils.frontend.OptionParser(
        components=components
    ).get_default_values()
    document = docutils.utils.new_document('<rst-doc>', settings=settings)
    parser.parse(text, document)
    return document


@pytest.mark.moto
def test_release_versions():
    # ensures versions in CHANGES.rst + __init__.py match
    init_version = version.parse(aiobotocore.__version__)

    changes_path = _root_path / 'CHANGES.rst'

    with changes_path.open('r') as f:
        changes_doc = _parse_rst(f.read())

    rst_ver_str = changes_doc[0][1][0][0]  # ex: 0.11.1 (2020-01-03)
    rst_prev_ver_str = changes_doc[0][2][0][0]

    rst_ver_groups = _rst_ver_date_str_re.match(rst_ver_str)
    rst_prev_ver_groups = _rst_ver_date_str_re.match(rst_prev_ver_str)

    rst_ver = version.parse(rst_ver_groups['version'])
    rst_prev_ver = version.parse(rst_prev_ver_groups['version'])

    # first the init version should match the rst version
    assert init_version == rst_ver

    # the current version must be greater than the previous version
    assert rst_ver > rst_prev_ver

    rst_date = rst_ver_groups['date']
    rst_prev_date = rst_prev_ver_groups['date']

    if rst_date == 'TBD':
        assert (
            rst_ver.is_prerelease
        ), 'Version must be prerelease if final release date not set'
    else:
        assert (
            not rst_ver.is_prerelease
        ), 'Version must not be prerelease if release date set'

        rst_date = datetime.strptime(rst_date, '%Y-%m-%d').date()
        rst_prev_date = datetime.strptime(rst_prev_date, '%Y-%m-%d').date()

        assert (
            rst_date > rst_prev_date
        ), 'Current release must be after last release'
