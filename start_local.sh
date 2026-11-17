#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if [ ! -f data/energy/20261026_predictions.csv ] || [ ! -f data/catalog.json ]; then
  echo 'Extract the complete V24 archive into a new folder first.'
  exit 1
fi
for task_file in static/ai.html static/ai.js local_ai.py local_access.py static/premium.css static/home.html static/home.js static/home.css static/product.css static/product.js source_records/matlab_validation/verified_runs.json source_records/matlab_validation/po_evidence.zip source_records/matlab_validation/inc_evidence.zip matlab_jobs.py static/matlab.html static/matlab.js static/matlab.css matlab_adapter/wg_task_entry.m matlab_adapter/wg_run_for_web.m matlab_adapter/wg_export_native.m matlab_adapter/wg_write_json.m matlab_adapter/wg_sha256.m matlab_adapter/SOURCE_FILES.json matlab_adapter/project/WG_PV_Environment.slx matlab_adapter/project/ControlThreePhaseSolarInverterParameters.m matlab_adapter/project/cloud_test_input.mat matlab_adapter/project/cloud_fast_input.mat matlab_adapter/project/cloud_slow_input.mat matlab_adapter/project/WG_PV_stage_config.mat paired_capture.py static/comparison.html static/comparison.js static/comparison.css data/simulink_comparison/po/signals.csv data/simulink_comparison/po/source.json data/simulink_comparison/inc/signals.csv data/simulink_comparison/inc/source.json simulink_bridge.py static/integration.html static/integration.js static/integration.css static/integration/model_manifest.json static/integration/WG_refined_station.glb software_models/generated/WG_refined_station.blend experiment_diagnostics.py static/bench_diagnostics.js static/diagnostic_state.mjs bench_experiments.py static/bench.html static/bench.js static/bench_scene.js static/scene_state.mjs static/bench.css server.py record_store.py live_simulation.py pv_physics.py spatial_model.py storage_sandbox.py dispatch_milp.py static/app.js static/entry.js static/landing.html static/workspace.css static/inspection.css static/prediction.css static/data_hub.css static/usability.css; do
  if [ ! -f "$task_file" ]; then
    echo 'Project files are missing. Extract the complete V24 archive into a new folder first.'
    exit 1
  fi
done
if [ ! -x .venv/bin/python ]; then python3 -m venv .venv; fi
if ! .venv/bin/python -c 'import fastapi,uvicorn,websockets' >/dev/null 2>&1; then
  .venv/bin/python -m pip install -r requirements.txt --timeout 90 --retries 2
fi
export PYTHONUTF8=1
exec .venv/bin/python -X utf8 server.py
