# scan-report-kit
#
#   make test                 offline unit tests
#   make check                tests + render both samples
#   make demo                 render examples/report.sample.ja.json into $(DEMO_OUT)
#   make vendor DEST=dir      copy scan_report_kit/ into a skill's scripts dir

PYTHON   ?= python3
# Make's path functions split on spaces. Preserve the actual filename and
# quote paths for the shell, including when this file is selected with -f.
shell_quote = '$(subst ','"'"',$(1))'
# Expand the hash after parsing so older Make versions do not start a comment.
hash_mark := \#
# Discard earlier entries if more than one makefile was loaded. MAKEFILE_LIST
# does not escape filename spaces, so test each remaining suffix as a path.
MAKEFILE_PATH := $(shell path=$(call shell_quote,$(MAKEFILE_LIST)); \
  while [ ! -f "$$path" ] && [ "$$path" != "$${path$(hash_mark)* }" ]; do \
    path=$${path$(hash_mark)* }; \
  done; printf '%s\n' "$$path")
REPO := $(shell path=$(call shell_quote,$(MAKEFILE_PATH)); \
  if [ "$${path$(hash_mark)/}" = "$$path" ]; then path="$$PWD/$$path"; fi; \
  printf '%s\n' "$${path%/*}")
DEMO_OUT ?= $(or $(TMPDIR),/tmp)/scan-report-kit-demo
LANG_OUT ?= ja
NO_PDF   ?=

.PHONY: help test check demo vendor

help: ## list targets
	@grep -E '^[a-z-]+:.*## ' $(call shell_quote,$(MAKEFILE_PATH)) \
	  | awk -F':.*## ' '{printf "  %-8s %s\n", $$1, $$2}'
	@echo "  vars:    DEST DEMO_OUT LANG_OUT NO_PDF PYTHON"

test: ## run offline unit tests
	@cd $(call shell_quote,$(REPO)) && PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m unittest discover -s tests -v

check: test ## tests, then render both samples without PDF
	@set -eu; cd $(call shell_quote,$(REPO)); \
	  out=$$(mktemp -d "$${TMPDIR:-/tmp}/scan-report-kit-check.XXXXXX"); \
	  trap 'rm -rf "$$out"' EXIT; trap 'exit 1' HUP INT TERM; \
	  for lang in en ja; do \
	    s=examples/report.sample.json; \
	    if [ "$$lang" = ja ]; then s=examples/report.sample.ja.json; fi; \
	    PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m scan_report_kit $$s --out "$$out/$$lang" --lang $$lang --no-pdf >/dev/null \
	      || { echo "MISS $$s does not render" >&2; exit 1; }; \
	  done; echo "check ok - both samples render"

demo: ## render the sample (dashboard + report + PDF) into DEMO_OUT
	@cd $(call shell_quote,$(REPO)) && PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m scan_report_kit \
	  examples/report.sample$(if $(filter ja,$(LANG_OUT)),.ja,).json \
	  --out $(call shell_quote,$(DEMO_OUT)) --lang $(call shell_quote,$(LANG_OUT)) $(if $(NO_PDF),--no-pdf,)

vendor: ## copy scan_report_kit/ into DEST (a skill's scripts directory)
	@[ -n $(call shell_quote,$(DEST)) ] || { echo "usage: make vendor DEST=/path/to/skill/scripts" >&2; exit 2; }
	@PYTHONDONTWRITEBYTECODE=1 $(PYTHON) $(call shell_quote,$(REPO)/scripts/vendor.py) $(call shell_quote,$(DEST))
