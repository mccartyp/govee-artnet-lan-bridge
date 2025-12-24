.PHONY: install-system install-user uninstall-system uninstall-user installer-help

PYTHON ?= python3
START ?= 1
SETCAP ?= 0

installer-help:
	@./scripts/install.sh --help

install-system:
	./scripts/install.sh install --system $(if $(filter 0,$(START)),--no-start,) $(if $(filter 1,$(SETCAP)),--setcap,) --python $(PYTHON)

install-user:
	./scripts/install.sh install --user $(if $(filter 0,$(START)),--no-start,) $(if $(filter 1,$(SETCAP)),--setcap,) --python $(PYTHON)

uninstall-system:
	./scripts/install.sh uninstall --system --python $(PYTHON)

uninstall-user:
	./scripts/install.sh uninstall --user --python $(PYTHON)
