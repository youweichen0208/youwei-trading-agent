"""Fixed chronological trial splits and descriptive paired scoring."""
from collections import Counter
from datetime import datetime
from statistics import mean


def _instant(value):
    return datetime.fromisoformat(value)


def validate_trial_start(spec, spec_sha256, data, events):
    events = [e for e in events if e.get('trial_id') == spec['trial_id']]
    if any(e['event_type'] in ('started', 'observed', 'completed', 'failed') for e in events):
        raise ValueError('trial already started; register a new attempt with its reason before rerunning')
    fixed = [e for e in events if e.get('spec_sha256')]
    if not fixed or fixed[-1]['spec_sha256'] != spec_sha256:
        raise ValueError('trial spec hash differs from pre-registration')
    expected = spec['population'].removeprefix('frozen_registration_').removesuffix('_current_panel_only')
    if data.get('references', {}).get('panel_registration_id') != expected:
        raise ValueError('trial panel differs from pre-registration')


def partition_rows(rows, sessions, spec):
    parts = {'train': [], 'validation': [], 'test': []}
    embargo = {}
    for part, prior_end in [('validation', spec['train_cutoff_end']),
                            ('test', spec['validation_cutoff_end'])]:
        following = sorted(s['date'] for s in sessions if s['date'] > prior_end)
        embargo[part] = following[64] if len(following) >= 65 else None
    for row in rows:
        d = row['cutoff'][:10]
        for part in parts:
            if not spec[f'{part}_cutoff_start'] <= d <= spec[f'{part}_cutoff_end']:
                continue
            item = dict(row)
            if part == 'train' and (row.get('assumed_label_available_at') is None or
                    _instant(row['assumed_label_available_at']) > _instant(spec['training_as_of'])):
                item['exclusion_reason'] = 'label_not_mature_at_fit'
            elif part != 'train' and (embargo[part] is None or row.get('entry_at') is None or
                    row['entry_at'][:10] < embargo[part]):
                item['exclusion_reason'] = 'partition_embargo'
            elif row.get('features') is None or row.get('label') is None:
                item['exclusion_reason'] = 'missing_input_or_label'
            parts[part].append(item)
            break
    return parts


def score_predictions(rows):
    paired, missing = [], Counter()
    for row in rows:
        if row.get('exclusion_reason') or row.get('label') is None or row.get('p_outperform') is None:
            missing[row.get('exclusion_reason', 'unavailable_prediction_or_label')] += 1
        else:
            paired.append(row)
    result = {'planned': len(rows), 'paired_scored': len(paired),
              'coverage': len(paired) / len(rows) if rows else None,
              'missing_reasons': dict(missing), 'baseline_brier': None,
              'quant_brier': None, 'paired_brier_difference': None,
              'calibration_bins': [], 'uncertainty': 'descriptive_only'}
    if not paired:
        return result
    result['baseline_brier'] = mean((0.5 - r['label'])**2 for r in paired)
    result['quant_brier'] = mean((r['p_outperform'] - r['label'])**2 for r in paired)
    result['paired_brier_difference'] = mean(
        (r['p_outperform'] - r['label'])**2 - (0.5 - r['label'])**2 for r in paired)
    result['quant_return_mae'] = mean(abs(r['expected_excess_return'] - r['excess_return']) for r in paired)
    result['baseline_return_mae'] = mean(abs(r['excess_return']) for r in paired)
    for i in range(10):
        group = [r for r in paired if min(int(r['p_outperform'] * 10), 9) == i]
        result['calibration_bins'].append({'bin': i, 'n': len(group),
            'mean_probability': mean(r['p_outperform'] for r in group) if group else None,
            'observed_rate': mean(r['label'] for r in group) if group else None})
    return result
