"""Run the pre-registered retrospective comparison once on frozen input.

No credentials or network. Refuses to overwrite prior outputs. Trial events
are appended before fitting and after completion, including failed runs.
"""
import argparse
from dataclasses import asdict
from datetime import UTC, date, datetime, time, timedelta
import hashlib
import json
from pathlib import Path
import re
import subprocess

from quant.dataset import build_dataset
from quant.logistic import LogisticSpec, export_model, predict_model, train_model
from quant.trial import partition_rows, score_predictions, validate_trial_start
from youwei_core.data.calendar import ET


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    with path.open('x') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')
    path.chmod(0o600)


def append_event(event):
    registry = Path('docs/trials/registry.md')
    records = [json.loads(s) for s in re.findall(r'```json\n(.*?)\n```', registry.read_text(), re.S)]
    event['prev_event_hash'] = records[-1]['event_hash']
    event['event_hash'] = hashlib.sha256(json.dumps(
        {k:v for k,v in event.items() if k not in ('event_hash','prev_event_hash')},
        ensure_ascii=False, sort_keys=True, separators=(',',':')).encode()).hexdigest()
    with registry.open('a') as f:
        f.write('\n### '+event['event_id']+'\n\n```json\n'+json.dumps(event,ensure_ascii=False,indent=2)+'\n```\n')


def run(args):
    spec_path, input_path = Path(args.spec), Path(args.input)
    spec, data = json.loads(spec_path.read_text()), json.loads(input_path.read_text())
    events = [json.loads(s) for s in re.findall(r'```json\n(.*?)\n```',
        Path('docs/trials/registry.md').read_text(), re.S)]
    validate_trial_start(spec, digest(spec_path), data, events)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise ValueError('trial output directory must be empty; never overwrite an observed trial')
    if data['pit_verified'] is not False or data['scope'] != 'retrospective_current_panel':
        raise ValueError('this trial requires honestly labelled retrospective input')
    now = datetime.now(UTC)
    code_paths = ['quant/dataset.py','quant/logistic.py','quant/trial.py',
                  'ops/run_s06_trial.py','uv.lock']
    provenance = {'input_sha256': digest(input_path), 'spec_sha256': digest(spec_path),
        'code_files': {p:digest(p) for p in code_paths},
        'code_commit': subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'code_state': 'working_tree_files_fixed_by_content_hash', 'started_at': now.isoformat()}
    event_base = {'trial_id': spec['trial_id'], 'actor_principal_id': 'codex_agent'}
    run_id = now.strftime('%Y%m%dT%H%M%SZ')
    append_event({**event_base, 'event_id':f'evt-trial-001-started-{run_id}',
        'event_type':'started', 'recorded_at':now.isoformat(), **provenance,
        'spec_ref':str(spec_path), 'result_ref':None})
    write_json(out/'provenance.json', provenance)
    try:
        cutoffs, d = [], date.fromisoformat(spec['cutoff_first'])
        while d <= date.fromisoformat(spec['cutoff_last']):
            cutoffs.append(datetime.combine(d, time(6), ET).isoformat())
            d += timedelta(days=7)
        dataset = build_dataset(bars_by_security=data['bars_by_security'],
            calendar_sessions=data['calendar_sessions'], cutoffs=cutoffs,
            panel_security_ids=data['panel_security_ids'],
            benchmark_security_id=data['references']['benchmark_security_id'],
            horizons_td=spec['horizons_td'])
        write_json(out/'cases.json', dataset)
        report = {'trial_id':spec['trial_id'], 'provenance':provenance,
            'scope':data['scope'], 'pit_verified':False, 'formal_campaign_allowed':False,
            'limitations':spec['limitations'], 'horizons':{}}
        candidate = {}
        for h in spec['horizons_td']:
            rows = [r for r in dataset['rows'] if r['horizon_td']==h]
            parts = partition_rows(rows, data['calendar_sessions'], spec)
            usable = [r for r in parts['train'] if not r.get('exclusion_reason')]
            model_spec = LogisticSpec(horizon_td=h, feature_names=tuple(spec['features']),
                c=spec['lr']['C'], ridge_alpha=spec['expected_return']['alpha'],
                seed=spec['lr']['seed'], max_iter=spec['lr']['max_iter'], tol=spec['lr']['tol'])
            def fit(training, cutoff):
                if len(training) < spec['minimum_training_rows']:
                    raise ValueError('insufficient training rows')
                return train_model([[r['features'][f] for f in spec['features']] for r in training],
                    [r['excess_return'] for r in training],
                    label_available_at=[datetime.fromisoformat(r['assumed_label_available_at']) for r in training],
                    training_cutoff=cutoff, spec=model_spec)
            fitted = fit(usable, datetime.fromisoformat(spec['training_as_of']))
            write_json(out/f'evaluation-model-d{h}.json', export_model(fitted))
            result = {'train_planned':len(parts['train']), 'train_used':len(usable),
                      'model_ref':f'evaluation-model-d{h}.json'}
            for part in ('validation','test'):
                selected = [r for r in parts[part] if not r.get('exclusion_reason')]
                predictions = predict_model(fitted, [[r['features'][f] for f in spec['features']] for r in selected]) if selected else []
                for row, prediction in zip(selected, predictions, strict=True):
                    row.update(p_outperform=prediction.p_outperform,
                               expected_excess_return=prediction.expected_excess_return)
                result[part] = score_predictions(parts[part])
                write_json(out/f'predictions-d{h}-{part}.json', parts[part])
            report['horizons'][str(h)] = result
            # Pre-registered refit uses the same specification; no test-dependent
            # model or feature choice occurs here.
            refit_rows = [r for r in rows if r['features'] is not None and r['label'] is not None
                and r['assumed_label_available_at'] is not None
                and datetime.fromisoformat(r['assumed_label_available_at']) <= now]
            candidate[str(h)] = export_model(fit(refit_rows, now))
        write_json(out/'candidate-models.json', candidate)
        report['artifacts'] = {p.name:digest(p) for p in out.glob('*.json')}
        write_json(out/'report.json', report)
        append_event({**event_base, 'event_id':f'evt-trial-001-completed-{run_id}',
            'event_type':'completed','recorded_at':datetime.now(UTC).isoformat(),
            'result_ref':str(out/'report.json'), 'result_sha256':digest(out/'report.json'),
            'scope':'retrospective_current_panel_not_formal_pit',
            'primary_test_result': report['horizons']['20']['test']})
        print(json.dumps({h: v['test'] for h,v in report['horizons'].items()},indent=2))
    except Exception as exc:
        append_event({**event_base, 'event_id':f'evt-trial-001-failed-{run_id}',
            'event_type':'failed','recorded_at':datetime.now(UTC).isoformat(),
            'failure_reason':str(exc), 'result_ref':None})
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec',default='docs/trials/trial-001-spec.v1.json')
    parser.add_argument('--input',required=True)
    parser.add_argument('--output-dir',required=True)
    run(parser.parse_args())
