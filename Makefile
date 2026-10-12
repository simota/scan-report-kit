# scan-report-kit
#
#   make test                 offline unit tests
#   make check                tests + render both samples
#   make demo                 render examples/report.sample.ja.json into $(DEMO_OUT)
#   make vendor DEST=dir      copy scan_report_kit/ into a skill's scripts dir

PYTHON   ?= python3
REPO     := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
DEMO_OUT ?= $(or $(TMPDIR),/tmp)/scan-report-kit-demo
LANG_OUT ?= ja
NO_PDF   ?=

.PHONY: help test check demo vendor

help: ## list targets
	@grep -E '^[a-z-]+:.*## ' $(lastword $(MAKEFILE_LIST)) \
	  | awk -F':.*## ' '{printf "  %-8s %s\n", $$1, $$2}'
	@echo "  vars:    DEST DEMO_OUT LANG_OUT NO_PDF PYTHON"

test: ## run offline unit tests
	@cd "$(REPO)" && PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m unittest discover -s tests -v

check: test ## tests, then render both samples without PDF
	@set -eu; cd "$(REPO)"; \
	  out=$$(mktemp -d "$${TMPDIR:-/tmp}/scan-report-kit-check.XXXXXX"); \
	  trap 'rm -rf "$$out"' EXIT; trap 'exit 1' HUP INT TERM; \
	  for lang in en ja; do \
	    s=examples/report.sample.json; \
	    if [ "$$lang" = ja ]; then s=examples/report.sample.ja.json; fi; \
	    PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m scan_report_kit $$s --out "$$out/$$lang" --lang $$lang --no-pdf >/dev/null \
	      || { echo "MISS $$s does not render" >&2; exit 1; }; \
	  done; echo "check ok - both samples render"

demo: ## render the sample (dashboard + report + PDF) into DEMO_OUT
	@cd "$(REPO)" && PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m scan_report_kit \
	  examples/report.sample$(if $(filter ja,$(LANG_OUT)),.ja,).json \
	  --out "$(DEMO_OUT)" --lang $(LANG_OUT) $(if $(NO_PDF),--no-pdf,)

vendor: ## copy scan_report_kit/ into DEST (a skill's scripts directory)
	@[ -n "$(DEST)" ] || { echo "usage: make vendor DEST=/path/to/skill/scripts" >&2; exit 2; }
	@PYTHONDONTWRITEBYTECODE=1 $(PYTHON) "$(REPO)/scripts/vendor.py" "$(DEST)"
