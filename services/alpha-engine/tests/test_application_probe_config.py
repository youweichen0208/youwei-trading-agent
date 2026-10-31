import argparse
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "application_probe", ROOT / "scripts/start_hermes_application_probe.py"
)
probe = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = probe
spec.loader.exec_module(probe)


@pytest.mark.parametrize(
    "massive,configured,browser,state_name,image,suffix,overlays,create_database,network",
    [
        (
            False,
            False,
            False,
            "hermes-application-state.json",
            "yta-bff:local",
            "mvp",
            (),
            True,
            False,
        ),
        (
            False,
            False,
            True,
            "hermes-browser-state.json",
            "yta-bff:local",
            "mvp",
            ("compose.hermes-browser-probe.yaml",),
            True,
            False,
        ),
        (
            False,
            True,
            False,
            "hermes-configured-state.json",
            "yta-bff:mvp",
            "mvp",
            ("compose.hermes-configured-probe.yaml",),
            True,
            False,
        ),
        (
            True,
            False,
            False,
            "massive-application-state.json",
            "yta-bff:massive",
            "massive",
            ("compose.hermes-configured-probe.yaml", "compose.massive-local.yaml"),
            False,
            True,
        ),
    ],
)
def test_probe_config_preserves_mode_layout(
    monkeypatch,
    tmp_path,
    massive,
    configured,
    browser,
    state_name,
    image,
    suffix,
    overlays,
    create_database,
    network,
):
    monkeypatch.setattr(probe, "ROOT", tmp_path)
    (tmp_path / ".local").mkdir()
    (tmp_path / ".local/massive-live-state.json").write_text(
        json.dumps({"database": "yta_massive_fixture_test"})
    )
    args = argparse.Namespace(massive=massive, configured=configured, browser=browser)

    config = probe.ProbeConfig.from_args(args)

    assert config.state == tmp_path / ".local" / state_name
    assert config.database == ("yta_massive_fixture_test" if massive else None)
    assert config.application_image == image
    assert config.image_suffix == suffix
    assert config.overlays == overlays
    assert config.create_database is create_database
    assert config.local_network is network
