"""Prepare and register an UNAPPROVED research release in the candidate DB.

Consumes frozen inputs and trial artifacts. Never creates approvals or campaigns.
"""
import argparse
import asyncio
import hashlib
import json
import os
import re
from pathlib import Path

from sqlalchemy import select
from quant.dataset import FEATURE_NAMES
from quant.logistic import MODEL_VERSION
from youwei_core.db.engine import make_engine
from youwei_core.db.meta import release_approvals, campaigns
from youwei_core.ledger.service import register_release, release_content_hash, sha256_hex
from youwei_core.ledger.training import (
    quant_module_artifact, register_training_manifest, training_manifest_content_hash,
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_candidate_inputs(trial, input_sha256, data, references, panel):
    if trial['provenance']['input_sha256'] != input_sha256:
        raise ValueError('frozen input hash differs from the trial')
    if data['references'] != references:
        raise ValueError('reference files differ from the frozen trial input')
    if panel['content_sha256'] != sha256_hex(panel['payload']):
        raise ValueError('panel bundle content hash mismatch')
    reg = panel['payload']['registration']
    if reg['id'] != references['panel_registration_id'] or reg['selected'] != data['panel_security_ids']:
        raise ValueError('panel membership differs from the frozen trial input')


async def build(root):
    references = json.loads((root/'references.json').read_text())
    panel = json.loads((root/'panel-bundle.json').read_text())
    trial = json.loads((root/'trial-001/report.json').read_text())
    completed = [json.loads(s) for s in re.findall(r'```json\n(.*?)\n```',
        Path('docs/trials/registry.md').read_text(), re.S)]
    completed = [e for e in completed if e.get('trial_id') == trial['trial_id'] and e['event_type'] == 'completed']
    if not completed or completed[-1]['result_sha256'] != digest(root/'trial-001/report.json'):
        raise ValueError('trial report differs from its registered completed result')
    models = json.loads((root/'trial-001/candidate-models.json').read_text())
    data = json.loads((root/'historical-input.json').read_text())
    validate_candidate_inputs(trial, digest(root/'historical-input.json'), data, references, panel)
    spec_path = Path('docs/trials/trial-001-spec.v1.json')
    if digest(spec_path) != trial['provenance']['spec_sha256']:
        raise ValueError('trial specification changed after observation')
    for filename, sha in trial['artifacts'].items():
        if digest(root/'trial-001'/filename) != sha:
            raise ValueError(f'trial artifact hash mismatch: {filename}')
    fs = 'price-tr-momentum-volatility-v1'
    common = {'preprocessing':'StandardScaler fit only on training rows; frozen means/scales',
        'fitting_window':'weekly 2017-01-07 through 2026-06-27; current-panel retrospective; same pre-registered parameters',
        'calibration':'none: fitted logistic probabilities; calibration not assumed',
        'label_maturation':'fixed target exit + 5 sessions is a historical assumption; actual vendor usable_at in input sources; current refit occurs after collection',
        'feature_set':fs}
    model_artifact = {'kind':'numeric-json', 'ref':str(root/'trial-001/candidate-models.json'),
                      'sha256':digest(root/'trial-001/candidate-models.json')}
    manifest = {
        'manifest_id':'tm-logistic-ridge-candidate-20261001-v1',
        'status':'candidate_unapproved', 'historical_pit_verified':False,
        'feature_sets':[{'feature_set_version':fs, 'features':[
            {'name':f,'kind':'daily_bars','definition':{
                'excess_momentum_20d':'20-session arithmetic excess close-to-close total return versus SPY; 21 closes',
                'excess_momentum_60d':'60-session arithmetic excess close-to-close total return versus SPY; 61 closes',
                'volatility_20d':'20 daily simple total returns; population std (ddof=0) times sqrt(252)',
            }[f], 'missing_policy':'any missing required calendar bar => unavailable; no imputation'} for f in FEATURE_NAMES]}],
        'models':[
            {'role':'baseline','model_version':'baseline-constant-v0','artifact':quant_module_artifact(),
             'feature_set':'none','preprocessing':'none','fitting_window':'none','calibration':'none',
             'label_maturation':'none: p=0.5 and expected_excess_return=0'},
            {'role':'quant_model','model_version':MODEL_VERSION,'artifact':model_artifact,**common}],
        'trial_ref':'trial-001-quant-lr-vs-baseline','trial_report_sha256':digest(root/'trial-001/report.json'),
        'spec_ref':str(spec_path),'spec_sha256':digest(spec_path),
        'data_input_sha256':trial['provenance']['input_sha256'],
        'limitations':trial['limitations'],
    }
    registration = json.loads(Path('docs/protocols/s00-registration.v2.json').read_text())
    protocol_refs = {}
    for item in registration['protocol_files']:
        p = Path('docs/protocols')/item['path']
        if digest(p) != item['sha256']:
            raise ValueError('registered protocol content hash mismatch')
        protocol_refs[item['path']] = item['sha256']
    release = {
        'release_id':'release-logistic-ridge-candidate-20261001-v1',
        'status':'candidate_unapproved', 'enabled_sources':['baseline','quant_model'],
        'fallback_policy':'phase1a-none', 'baseline_version':'baseline-constant-v0',
        'quant_model_version':MODEL_VERSION, 'feature_set_version':fs,
        'training_manifest_ref':manifest['manifest_id'],
        'training_manifest_sha256':training_manifest_content_hash(manifest),
        'quant_artifacts':{h:{'content':m,'content_sha256':sha256_hex(m)} for h,m in models.items()},
        'panel_registration_id':references['panel_registration_id'],
        'panel_bundle_sha256':panel['content_sha256'], 'references':references,
        'protocol_refs':protocol_refs, 'target_horizons_td':[1,20,60],
        'primary_metric':'D20 paired Brier quant minus baseline',
        'memory_policy':'empty', 'prompt':'not_enabled', 'llm':'not_enabled',
        'historical_pit_verified':False, 'evidence_scope':'retrospective_current_panel',
        'trial_report_sha256':digest(root/'trial-001/report.json'),
        'code_files':{p:digest(Path(p)) for p in ['quant/logistic.py','quant/dataset.py',
            'youwei_core/ledger/model_registry.py','youwei_core/ledger/pipeline.py','uv.lock']},
    }
    engine = make_engine(os.environ['YOUWEI_DATABASE_URL'])
    try:
        tm = await register_training_manifest(engine, manifest=manifest)
        record = await register_release(engine, release_id=release['release_id'], manifest=release)
        async with engine.connect() as conn:
            approvals = (await conn.execute(select(release_approvals.c.id))).all()
            registered_campaigns = (await conn.execute(select(campaigns.c.id))).all()
        summary = {'release_id':record.release_id,'release_content_sha256':record.release_content_sha256,
            'training_manifest_id':tm.manifest_id,'training_manifest_sha256':tm.content_sha256,
            'human_approval_granted':False,'formal_campaign_allowed':False,
            'approval_records_in_candidate_db':len(approvals),'campaigns_in_candidate_db':len(registered_campaigns),
            'primary_test_brier_difference':trial['horizons']['20']['test']['paired_brier_difference'],
            'recommendation':'no demonstrated improvement; review limitations before any prospective experiment approval'}
        for name,obj in [('training-manifest.json',manifest),('release-candidate.json',release),('release-summary.json',summary)]:
            path=root/name
            if path.exists() and json.loads(path.read_text()) != obj:
                raise ValueError('refuse to overwrite differing candidate content')
            path.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n');path.chmod(0o600)
        print(json.dumps(summary,ensure_ascii=False,indent=2))
    finally:
        await engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate-dir',required=True)
    asyncio.run(build(Path(parser.parse_args().candidate_dir)))
