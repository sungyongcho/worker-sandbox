.PHONY: test package

PYTHON ?= python3

test:
	$(PYTHON) -B -m unittest discover -s tests -v

package:
	$(PYTHON) -m build --wheel
