# Some simple testing tasks (sorry, UNIX only).

FLAGS=

flake: checkrst
	flake8 aiobotocore tests examples setup.py

test: flake
	python3 -m pytest -s $(FLAGS) ./tests/

vtest:
	python3 -m pytest -s -v $(FLAGS) ./tests/

checkrst:
	python setup.py check -rms

cov cover coverage: flake
	python3 -m pytest -s -v --cov-report term --cov-report html --cov aiobotocore ./tests
	@echo "open file://`pwd`/htmlcov/index.html"

# BOTO_CONFIG solves https://github.com/travis-ci/travis-ci/issues/7940
mototest:
	BOTO_CONFIG=/dev/null python3 -m pytest -v -m moto --cov-report term --cov-report html --cov aiobotocore tests
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
