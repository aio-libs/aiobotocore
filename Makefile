# Some simple testing tasks (sorry, UNIX only).

isort:
	isort -rc aiobotocore
	isort -rc tests
	isort -rc examples

flake: .flake

.flake: $(shell find aiobotocore -type f) \
	    $(shell find tests -type f) \
	    $(shell find examples -type f)
	flake8 aiobotocore tests examples
	python setup.py check -rms
	@if ! isort -c -rc aiobotocore tests examples; then \
            echo "Import sort errors, run 'make isort' to fix them!!!"; \
            isort --diff -rc aiobotocore tests examples; \
            false; \
	fi

test:
	$(eval export PYTHONPATH=.)
	py.test -s -rs -m moto

vtest:
	$(eval export PYTHONPATH=.)
	py.test -svvv -rs -m moto

cov cover coverage: flake
	$(eval export PYTHONPATH=.)
	py.test -svvv -rs -m moto --cov-report=term --cov-report=html --cov=aiobotocore
	@echo "open file://`pwd`/htmlcov/index.html"

aws-test:
	$(eval export PYTHONPATH=.)
	py.test -s -rs

aws-vtest:
	$(eval export PYTHONPATH=.)
	py.test -svvv -rs

aws-cov aws-cover aws-coverage: flake
	$(eval export PYTHONPATH=.)
	py.test -svvv -rs --cov-report=term --cov-report=html --cov=aiobotocore
	@echo "open file://`pwd`/htmlcov/index.html"

# BOTO_CONFIG solves https://github.com/travis-ci/travis-ci/issues/7940
test-ci: flake
	$(eval export PYTHONPATH=.)
	$(eval export BOTO_CONFIG=/dev/null)
	py.test -svvv -rs -m moto --cov-report=term --cov-report=html --cov=aiobotocore
	@echo "open file://`pwd`/htmlcov/index.html"

clean-pip:
	pip freeze | grep -v "^-e" | xargs pip uninstall -y

clean:
	@rm -rf `find . -name __pycache__`
	@rm -rf `find . -type f -name '*.py[co]' `
	@rm -rf `find . -type f -name '*~' `
	@rm -rf `find . -type f -name '.*~' `
	@rm -rf `find . -type f -name '@*' `
	@rm -rf `find . -type f -name '#*#' `
	@rm -rf `find . -type f -name '*.orig' `
	@rm -rf `find . -type f -name '*.rej' `
	@rm -rf .coverage
	@rm -rf coverage.xml
	@rm -rf htmlcov
	@rm -rf .pytest_cache
	@rm -rf build
	@rm -rf cover
	@make -C docs clean
	@python setup.py clean
	@rm -rf .develop
	@rm -rf .flake
	@rm -rf .install-deps
	@rm -rf aiobotocore.egg-info

doc:
	make -C docs html
	@echo "open file://`pwd`/docs/_build/html/index.html"

.PHONY: all flake test aws-test aws-vtest vtest cov aws-cov clean doc test-ci
