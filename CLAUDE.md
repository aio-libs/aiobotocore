# Pre-commit checks

Run before committing:

```
uv run pre-commit run --all --show-diff-on-failure
```

This runs: `check-yaml`, `end-of-file-fixer`, `trailing-whitespace`, `ruff-check`, `ruff-format`, `uv-lock`, `yamllint --strict`, `check-github-workflows`, `check-github-workflows-require-timeout`, `check-dependabot`, `check-readthedocs`.

Key constraints:
- YAML lines must be ≤80 chars (use `# yamllint disable-line rule:line-length` for unavoidable exceptions like SHA-pinned actions)
- All workflow jobs must have `timeout-minutes`
- Python code formatted with `ruff`

# Tests

```
uv run make mototest    # moto-based tests (CI runs these)
uv run pytest -sv tests/test_patches.py  # hash validation
```

# Botocore version updates

See `CONTRIBUTING.rst` "How to Upgrade Botocore" section. Key files:
- `pyproject.toml` — botocore version range
- `aiobotocore/__init__.py` — aiobotocore version
- `tests/test_patches.py` — SHA1 hashes of patched botocore code
- `CHANGES.rst` — changelog
