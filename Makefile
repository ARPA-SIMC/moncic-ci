PYTHON_ENVIRONMENT := PYTHONASYNCDEBUG=1 PYTHONDEBUG=1 PYTHONDEVMODE=1

check: flake8 mypy

format: pyupgrade autoflake isort black

pyupgrade:
	pyupgrade --exit-zero-even-if-changed --py39-plus monci $(shell find moncic tests -name "*.py")

black:
	black monci moncic tests

autoflake:
	autoflake --in-place --recursive monci moncic tests

isort:
	isort monci moncic tests

flake8:
	pyflakes3 monci moncic tests

mypy:
	mypy monci moncic tests

unittest:
	$(PYTHON_ENVIRONMENT) nose2-3

coverage:
	$(PYTHON_ENVIRONMENT) sudo nose2-3 -C --coverage-report html

clean:

.PHONY: check pyupgrade black mypy unittest coverage clean
