"""Parse, extract from, and validate ``CHANGES.rst``.

Single source of truth for the changelog file format. Used by:

* ``tests/test_version.py`` -- format invariants + ``__init__.py`` agreement
* ``.github/workflows/auto-release-on-merge.yml`` -- extracts the new
  release's bullets to use as GitHub Release notes
* ``plugins/aiobotocore-bot/skills/draft-release`` -- (optional)
  post-write sanity check after generating a new entry

Stdlib-only on purpose: the auto-release workflow runs against a sparse
checkout without installing the project's dev dependencies. Importing
``packaging.version`` would force the workflow to ``pip install`` first.
The simple ``(major, minor, patch, dev)`` tuple comparison below is
enough for our X.Y.Z[.devN] versioning.

CLI shape::

    python scripts/changelog.py extract --version 3.7.0
    python scripts/changelog.py validate [--expected-top-version 3.7.0]

Exit code is 0 on success, non-zero with a message on stderr otherwise.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

# Header line shape: ``X.Y.Z (YYYY-MM-DD)``. ``X.Y.Z.devN`` is also
# accepted. Earlier versions of this format permitted a ``TBD`` date
# placeholder, dropped now that ``draft-release`` always writes the
# date when assembling the entry.
_VERSION_HEADER_RE = re.compile(
    r"^(?P<version>\d+\.\d+\.\d+(?:\.dev\d+)?) "
    r"\((?P<date>\d{4}-\d{2}-\d{2})\)\s*$"
)


@dataclass
class Entry:
    version: str
    date: str  # ``YYYY-MM-DD``
    body: str  # bullets, leading/trailing blank lines stripped


def parse(text: str) -> list[Entry]:
    """Parse ``CHANGES.rst`` text into entries, newest first.

    The expected layout is::

        Changes
        -------

        X.Y.Z (YYYY-MM-DD)
        ^^^^^^^^^^^^^^^^^^
        * bullet
        * bullet

        X.Y.(Z-1) (YYYY-MM-DD)
        ^^^^^^^^^^^^^^^^^^^^^^
        * bullet

    Top-of-file lines before the first version header are ignored
    (they're the file header). The ``^^^^`` row immediately below
    each version header is also ignored (it's the RST underline,
    not content). Subsequent version headers terminate the previous
    entry's body.
    """
    entries: list[Entry] = []
    current_header: dict[str, str] | None = None
    current_body: list[str] = []
    skip_underline = False
    for line in text.splitlines():
        if m := _VERSION_HEADER_RE.match(line):
            if current_header is not None:
                entries.append(
                    Entry(
                        current_header["version"],
                        current_header["date"],
                        _strip(current_body),
                    )
                )
            current_header = m.groupdict()
            current_body = []
            skip_underline = True
            continue
        if skip_underline:
            skip_underline = False
            continue
        if current_header is not None:
            current_body.append(line)
    if current_header is not None:
        entries.append(
            Entry(
                current_header["version"],
                current_header["date"],
                _strip(current_body),
            )
        )
    return entries


def _strip(lines: list[str]) -> str:
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def extract(text: str, version: str) -> str:
    """Return the body of the entry for ``version``. Raises if absent."""
    for entry in parse(text):
        if entry.version == version:
            return entry.body
    raise LookupError(f"no entry for version {version!r} in CHANGES.rst")


def _version_tuple(v: str) -> tuple[int, ...]:
    """``3.7.0`` -> ``(3, 7, 0)``; ``3.7.0.dev1`` -> ``(3, 7, 0, 1)``."""
    main, _, dev = v.partition(".dev")
    parts = [int(p) for p in main.split(".")]
    if dev:
        parts.append(int(dev))
    return tuple(parts)


def validate(
    text: str,
    *,
    expected_top_version: str | None = None,
    full_history: bool = False,
) -> None:
    """Assert structural invariants. Raises ``ValueError`` on violation.

    Always checked:

    * At least one parseable entry.
    * If ``expected_top_version`` provided, top entry matches it
      (this is how the workflow + ``test_version.py`` cross-check
      ``__init__.py`` against ``CHANGES.rst``).
    * **Top entry vs the previous entry** -- version is strictly
      greater, and date is non-increasing. This is the check that
      matters for *new* releases and matches the existing
      ``test_version.py`` scope.

    Opt-in (``full_history=True``): same checks across all consecutive
    pairs in the file. Useful when auditing the historical changelog,
    not for the per-release gate -- the project's older entries
    contain ordering quirks from before this validator existed.
    """
    entries = parse(text)
    if not entries:
        raise ValueError("CHANGES.rst has no parseable entries")

    if (
        expected_top_version is not None
        and entries[0].version != expected_top_version
    ):
        raise ValueError(
            f"top entry version {entries[0].version!r} does not match "
            f"expected {expected_top_version!r}"
        )

    pairs = (
        list(zip(entries, entries[1:]))
        if full_history
        else entries[1:2] and [(entries[0], entries[1])]
    )

    for prev, cur in pairs:
        if _version_tuple(prev.version) <= _version_tuple(cur.version):
            raise ValueError(
                f"version order broken: {prev.version} should be > {cur.version}"
            )
        if date.fromisoformat(prev.date) < date.fromisoformat(cur.date):
            raise ValueError(
                f"date order broken: {prev.version} ({prev.date}) "
                f"is older than {cur.version} ({cur.date})"
            )


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_extract = sub.add_parser(
        "extract", help="print the body of the entry for --version"
    )
    p_extract.add_argument("--version", required=True)
    p_extract.add_argument("--path", default="CHANGES.rst")

    p_validate = sub.add_parser("validate", help="check format invariants")
    p_validate.add_argument("--expected-top-version", default=None)
    p_validate.add_argument("--path", default="CHANGES.rst")
    p_validate.add_argument(
        "--full-history",
        action="store_true",
        help="check all consecutive pairs (default: top entry vs previous only)",
    )

    args = parser.parse_args(argv)
    text = Path(args.path).read_text(encoding="utf-8")

    if args.cmd == "extract":
        try:
            print(extract(text, args.version))
        except LookupError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        return 0

    if args.cmd == "validate":
        try:
            validate(
                text,
                expected_top_version=args.expected_top_version,
                full_history=args.full_history,
            )
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        return 0

    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
