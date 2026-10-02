"""Dashboard static view rendering tests.

The dashboard is native ES modules with no build chain (S10a), so view
rendering behavior is verified with Node's built-in test runner
(``node --test``). This wrapper makes ``uv run --frozen pytest`` fail —
not skip — when no Node runtime can be found: a check that cannot run
must not be reported as passed.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
VIEW_TEST_GLOB = "apps/dashboard/static/tests/*.test.mjs"

_NODE_FALLBACKS = ("/opt/homebrew/bin/node", "/usr/local/bin/node")


def _node_binary() -> str:
    found = shutil.which("node")
    if found:
        return found
    for candidate in _NODE_FALLBACKS:
        if Path(candidate).exists():
            return candidate
    pytest.fail(
        "node runtime not found (PATH, /opt/homebrew/bin, /usr/local/bin); "
        "dashboard view rendering tests require Node >= 18"
    )


def test_dashboard_view_rendering():
    node = _node_binary()
    result = subprocess.run(
        [node, "--test", VIEW_TEST_GLOB],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=120,
    )
    assert result.returncode == 0, (
        "dashboard view rendering tests failed "
        f"(node --test {VIEW_TEST_GLOB}):\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
