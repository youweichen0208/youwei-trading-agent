"""Full real-container -> Worker/tools/PostgreSQL/Quant/Canvas acceptance.

HERMES_IMAGE=sha256:... TEST_DATABASE_URL=... uv run pytest -q \
  tests/hermes/container_system_integration.py
Only model decisions and market bars are synthetic. No production report.
"""

import os

from system_integration import run_three_profiles


def test_three_containers_with_real_tools_worker_and_canvas(tmp_path):
    run_three_profiles(tmp_path, image=os.environ["HERMES_IMAGE"])
