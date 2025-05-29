PYTHON_ENVIRONMENT := PYTHONASYNCDEBUG=1 PYTHONDEBUG=1 PYTHONDEVMODE=1

check: flake8 mypy

format: pyupgrade autoflake isort black

pyupgrade:
	pyupgrade --exit-zero-even-if-changed --py312-plus monci $(shell find moncic tests itests -name "*.py") test

black:
	black monci moncic tests test

autoflake:
	autoflake --in-place --recursive monci moncic tests itests test

isort:
	isort monci moncic tests itests test

flake8:
	flake8 monci moncic tests itests test

mypy:
	mypy monci moncic tests itests
	mypy test

unittest:
	$(PYTHON_ENVIRONMENT) nose2-3

coverage:
	$(PYTHON_ENVIRONMENT) nose2-3 -C --coverage-report html

clean:

.PHONY: check pyupgrade black mypy unittest coverage clean
