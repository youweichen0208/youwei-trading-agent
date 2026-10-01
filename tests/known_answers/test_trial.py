from quant.trial import partition_rows, score_predictions
import pytest


def test_training_does_not_include_labels_maturing_after_fit_time():
    spec = {'train_cutoff_start':'2020-01-01', 'train_cutoff_end':'2022-12-31',
            'training_as_of':'2023-01-01T00:00:00Z',
            'validation_cutoff_start':'2023-01-01','validation_cutoff_end':'2023-12-31',
            'test_cutoff_start':'2024-01-01','test_cutoff_end':'2025-12-31'}
    row = {'cutoff':'2022-12-24T11:00:00Z', 'entry_at':'2022-12-27T14:30:00Z',
           'assumed_label_available_at':'2023-02-01T21:00:00Z',
           'feature_status':'available', 'label_status':'resolved'}
    parts = partition_rows([row], [], spec)
    assert parts['train'][0]['exclusion_reason'] == 'label_not_mature_at_fit'


def test_scoring_preserves_missing_cases_and_pairs_same_case():
    rows = [{'label':1, 'excess_return':0.1, 'p_outperform':0.8, 'expected_excess_return':0.05},
            {'label':0, 'excess_return':-0.1, 'p_outperform':0.4, 'expected_excess_return':-0.05},
            {'label':None, 'p_outperform':None, 'exclusion_reason':'missing_input'}]
    result = score_predictions(rows)
    assert result['planned'] == 3 and result['paired_scored'] == 2
    assert abs(result['quant_brier'] - 0.1) < 1e-12
    assert abs(result['paired_brier_difference'] + 0.15) < 1e-12
    assert result['missing_reasons'] == {'missing_input': 1}


def test_trial_cannot_be_repeated_by_using_a_new_output_directory():
    from quant.trial import validate_trial_start
    with pytest.raises(ValueError, match='already started'):
        validate_trial_start({'trial_id':'trial-1'}, 'abc', {},
                             [{'trial_id':'trial-1','event_type':'started'}])


def test_trial_must_match_pre_registered_spec_hash_and_panel():
    from quant.trial import validate_trial_start
    spec = {'trial_id':'trial-1','population':'frozen_registration_panel-1_current_panel_only'}
    events = [{'trial_id':'trial-1','event_type':'corrected','spec_sha256':'frozen'}]
    with pytest.raises(ValueError, match='spec hash'):
        validate_trial_start(spec, 'changed', {}, events)
    with pytest.raises(ValueError, match='panel'):
        validate_trial_start(spec, 'frozen', {'references':{'panel_registration_id':'different'}}, events)
