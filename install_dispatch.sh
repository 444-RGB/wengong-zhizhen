#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if [ ! -x .venv/bin/python ]; then echo 'Run bash start_local.sh once first.'; exit 1; fi
.venv/bin/python -m pip install -r requirements-dispatch.txt --timeout 90 --retries 2
.venv/bin/python -c 'from scipy.optimize import milp; import numpy; print("Dispatch solver ready. Restart the platform server.")'
