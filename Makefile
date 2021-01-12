# Some simple testing tasks (sorry, UNIX only).

FLAGS=

flake: checkrst
	pipenv run python3 -m flake8 --format=abspath

test: flake
	pipenv run python3 -Wd -m pytest -s -vv $(FLAGS) ./tests/

vtest:
	pipenv run python3 -Wd -X tracemalloc=5 -X faulthandler -m pytest -s -vv $(FLAGS) ./tests/

checkrst:
	pipenv run python3 setup.py check -rms

cov cover coverage: flake
	pipenv run python3 -Wd -m pytest -s -vv --cov-report term --cov-report html --cov aiobotocore ./tests
	@echo "open file://`pwd`/htmlcov/index.html"

# BOTO_CONFIG solves https://github.com/travis-ci/travis-ci/issues/7940
mototest:
	docker pull alpine
	docker pull lambci/lambda:python3.8
	BOTO_CONFIG=/dev/null pipenv run python3 -Wd -X tracemalloc=5 -X faulthandler -m pytest -vv -m moto -n auto --cov-report term --cov-report html --cov --log-cli-level=DEBUG aiobotocore tests
	@echo "open file://`pwd`/htmlcov/index.html"


clean:
	rm -rf `find . -name __pycache__`
	rm -f `find . -type f -name '*.py[co]' `
	rm -f `find . -type f -name '*~' `
	rm -f `find . -type f -name '.*~' `
	rm -f `find . -type f -name '@*' `
	rm -f `find . -type f -name '#*#' `
	rm -f `find . -type f -name '*.orig' `
	rm -f `find . -type f -name '*.rej' `
	rm -f .coverage
	rm -rf coverage
	rm -rf build
	rm -rf cover
	rm -rf dist

doc:
	make -C docs html
	@echo "open file://`pwd`/docs/_build/html/index.html"

.PHONY: all flake test vtest cov clean doc
