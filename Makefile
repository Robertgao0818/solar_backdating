# solar_backdating — developer convenience targets.
# Source scripts/activate_env.sh first so PYTHONPATH and the shared venv are set.
#
#   source scripts/activate_env.sh && make repro-check

SHELL := /bin/bash
PY ?= python

.PHONY: repro-check

repro-check:
	$(PY) -m pytest \
		tests/temporal/test_merge_ct_scan_states.py \
		tests/temporal/test_repro_r1_contract.py \
		tests/temporal/test_infer_install_dates_phase0.py \
		-q --tb=short
