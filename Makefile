.DEFAULT_GOAL := check

UVX ?= uvx
PYTHON ?= python3
SHELLCHECK ?= shellcheck
TEST_JOBS ?= 4

# Keep local formatting and CI consistent. These are development-only tools.
RUFF_VERSION := 0.16.9
SHFMT_PY_VERSION := 4.2.0
RUFF = $(UVX) --from ruff==$(RUFF_VERSION) ruff
SHFMT = $(UVX) --from shfmt-py==$(SHFMT_PY_VERSION) shfmt
SHELL_FILES := $(wildcard *.sh src/*.sh tools/*.sh) src/deckthere-root

TEST_TARGETS := $(patsubst tests/test_%.py,test-%,$(wildcard tests/test_*.py))

.PHONY: fmt lint test test-modules test-qt check $(TEST_TARGETS)

fmt:
	$(RUFF) check --fix .
	$(RUFF) format .
	$(SHFMT) -i 2 -ci -w $(SHELL_FILES)

lint:
	$(RUFF) check .
	$(RUFF) format --check .
	$(SHFMT) -i 2 -ci -d $(SHELL_FILES)
	$(SHELLCHECK) $(SHELL_FILES)
	@for script in $(SHELL_FILES); do bash -n "$$script" || exit; done

# Separate processes isolate environment patches, signals and Qt's application.
# GNU make bounds concurrency and reports failure if any module fails.
test:
	$(MAKE) --no-print-directory -j$(TEST_JOBS) --output-sync=target test-modules

test-modules: $(TEST_TARGETS)

$(TEST_TARGETS): test-%:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m unittest discover -s tests -p 'test_$*.py' -v -b

# Optional Qt coverage only: do not repeat all core tests after installing Qt.
# Require its imports so a missing/broken runtime cannot silently skip this job.
test-qt:
	$(PYTHON) -c 'from PySide6 import QtCore, QtGui, QtQml, QtQuick, QtTest'
	QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m unittest discover -s tests -p test_ui.py -v -b

check: lint test
