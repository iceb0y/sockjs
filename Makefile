# Some simple testing tasks (sorry, UNIX only).

FLAGS=


flake:
#	python setup.py check -rms
	flake8 sockjs tests examples

develop:
	python setup.py develop

test: flake develop
	nosetests -s $(FLAGS) ./tests/

vtest: flake develop
	nosetests -s -v $(FLAGS) ./tests/

cov cover coverage: flake develop
	@coverage erase
	@coverage run -m nose -s $(FLAGS) tests
	@coverage combine
	@coverage report
	@coverage html
	@echo "open file://`pwd`/coverage/index.html"

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
	make -C docs clean
	python setup.py clean

doc:
	make -C docs html
	@echo "open file://`pwd`/docs/_build/html/index.html"

.PHONY: all build venv flake test vtest testloop cov clean doc
