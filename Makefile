# Some simple testing tasks (sorry, UNIX only).

FLAGS=

LIB=aiobotocore

init: poetry
	poetry run pip install --upgrade pip
	poetry install -v --no-interaction --extras boto3

flake: package-check
	poetry run python3 -m flake8 --format=abspath

test: flake
	poetry run python3 -Wd -m pytest -s -vv $(FLAGS) ./tests/

vtest:
	poetry run python3 -Wd -X tracemalloc=5 -X faulthandler -m pytest -s -vv $(FLAGS) ./tests/

cov cover coverage: flake
	poetry run python3 -Wd -m pytest -s -vv --cov-report term --cov-report html --cov $(LIB) ./tests
	echo "open file://`pwd`/htmlcov/index.html"

# BOTO_CONFIG solves https://github.com/travis-ci/travis-ci/issues/7940
mototest:
	docker pull alpine
	docker pull lambci/lambda:python3.8
	BOTO_CONFIG=/dev/null pipenv run python3 -Wd -X tracemalloc=5 -X faulthandler -m pytest -vv -m moto -n auto --cov-report term --cov-report html --cov-report xml --cov=aiobotocore --cov=tests --log-cli-level=DEBUG aiobotocore tests
	@echo "open file://`pwd`/htmlcov/index.html"

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

doc:
	make -C docs html
	@echo "open file://`pwd`/docs/_build/html/index.html"

typehint: clean poetry
	poetry run mypy --follow-imports=skip $(LIB) tests

package: clean poetry
	poetry check
	poetry build

package-check: package
	poetry run twine check dist/*

publish: package-check
	poetry publish

poetry:
	@if ! command -v poetry > /dev/null; then \
		curl -sSL https://raw.githubusercontent.com/python-poetry/poetry/master/get-poetry.py | python - ; \
		source "$(HOME)/.poetry/env" ; \
	fi

poetry-export: poetry
	poetry export --without-hashes -f requirements.txt -o requirements.txt
	sed -i -e 's/^-e //g' requirements.txt


.PHONY: init typehint package package-check poetry poetry-export
.PHONY: all flake test vtest cov clean doc
