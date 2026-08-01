# Some simple testing tasks (sorry, UNIX only).
#
# These targets are thin aliases. The test contract itself -- import mode,
# marker strictness, warning filters, testpaths, coverage paths -- lives in
# pyproject.toml, so a bare ``pytest`` behaves the same as CI and the suite runs
# straight out of an unpacked sdist, where this file is not what CI invokes.
#
# Note ``pytest`` and never ``python -m pytest``: the latter puts the working
# directory on sys.path, which shadows an installed aiobotocore with the source
# tree sitting next to it.

# ?= is conditional assign, so users can pass options on the CLI instead of manually editing this file
HTTP_BACKEND?='all'
FLAGS?=

# ``-X tracemalloc=5 -X faulthandler`` equivalents, now that we no longer go
# through ``python -m``.
TRACE_ENV=PYTHONTRACEMALLOC=5 PYTHONFAULTHANDLER=1

pre-commit:
	pre-commit run --all --show-diff-on-failure

test: pre-commit
	pytest -s -vv $(FLAGS)

vtest:
	$(TRACE_ENV) pytest -s -vv $(FLAGS)

cov cover coverage: pre-commit
	pytest -s -vv --cov --cov-report term --cov-report html $(FLAGS)
	@echo "open file://`pwd`/htmlcov/index.html"

mototest:
	$(TRACE_ENV) pytest -vv -m "not localonly" -n auto --reruns 1 \
		--cov --cov-report term --cov-report html --cov-report xml \
		--log-cli-level=DEBUG --http-backend=$(HTTP_BACKEND) $(FLAGS)

clean:
	rm -rf `find . -name __pycache__`
	rm -rf `find . -name .pytest_cache`
	rm -rf `find . -name *.egg-info`
	rm -f `find . -type f -name '*.py[co]' `
	rm -f `find . -type f -name '*~' `
	rm -f `find . -type f -name '.*~' `
	rm -f `find . -type f -name '@*' `
	rm -f `find . -type f -name '#*#' `
	rm -f `find . -type f -name '*.orig' `
	rm -f `find . -type f -name '*.rej' `
	rm -f .coverage*
	rm -rf coverage
	rm -rf coverage.xml
	rm -rf htmlcov
	rm -rf build
	rm -rf cover
	rm -rf dist

doc docs:
	uv run --group docs sphinx-build -W -b html docs docs/_build/html
	@echo "open file://`pwd`/docs/_build/html/index.html"

.PHONY: all pre-commit test vtest cov cover coverage mototest clean doc docs
