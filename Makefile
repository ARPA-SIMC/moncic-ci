PYTHON_ENVIRONMENT := PYTHONASYNCDEBUG=1 PYTHONDEBUG=1 PYTHONDEVMODE=1

check: flake8 mypy

format: pyupgrade autoflake isort black

pyupgrade:
	pyupgrade --exit-zero-even-if-changed --py312-plus monci $(shell find moncic tests itests -name "*.py")

black:
	black monci moncic tests

autoflake:
	autoflake --in-place --recursive monci moncic tests itests

isort:
	isort monci moncic tests itests

flake8:
	flake8 monci moncic tests itests

mypy:
	mypy monci moncic tests itests

unittest:
	$(PYTHON_ENVIRONMENT) nose2-3

coverage:
	$(PYTHON_ENVIRONMENT) nose2-3 -C --coverage-report html

clean:

.PHONY: check pyupgrade black mypy unittest coverage clean
