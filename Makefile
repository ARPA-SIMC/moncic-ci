PYTHON_ENVIRONMENT := PYTHONASYNCDEBUG=1 PYTHONDEBUG=1 PYTHONDEVMODE=1

check: flake8 mypy

format: pyupgrade autoflake isort black

pyupgrade:
	pyupgrade --exit-zero-even-if-changed --py312-plus monci $(shell find moncic itests -name "*.py") test

black:
	black monci moncic itests test

autoflake:
	autoflake --in-place --recursive monci moncic itests test

isort:
	isort monci moncic itests test

flake8:
	flake8 monci moncic itests test

mypy:
	mypy monci moncic itests
	mypy test

unittest:
	$(PYTHON_ENVIRONMENT) pytest

coverage:
	$(PYTHON_ENVIRONMENT) pytest --cov=moncic --cov-report html 

clean:

.PHONY: check pyupgrade black mypy unittest coverage clean
