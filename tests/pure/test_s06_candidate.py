import importlib.util
import json
from pathlib import Path

import pytest


def load_script(name):
    path = Path(__file__).resolve().parents[2] / 'ops' / f'{name}.py'
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frozen_candidate_file_cannot_be_overwritten(tmp_path):
    write = load_script('collect_s06_trial_data').write_json
    p = tmp_path/'frozen.json'
    write(p, {'value':1})
    write(p, {'value':1})
    with pytest.raises(ValueError, match='frozen'):
        write(p, {'value':2})
    assert json.loads(p.read_text()) == {'value':1}


def test_release_rejects_mixed_trial_input_and_reference_files():
    module = load_script('build_s06_release')
    with pytest.raises(ValueError, match='input hash'):
        module.validate_candidate_inputs({'provenance':{'input_sha256':'original'}},
                                         'different', {}, {}, {})
