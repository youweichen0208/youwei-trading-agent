"""Bounded public-source regression; no model calls, no credentials printed."""
import json
import sys
from datetime import datetime, timezone, timedelta
sys.path.insert(0, '/opt/youwei-assistant')
from finance import FinancialTools
from trading_core import PriceQuery

query = {'symbol': 'AAPL', 'start': '2026-07-05', 'end': '2026-10-06'}
PriceQuery(**query)
today = datetime.now(timezone.utc).date()
PriceQuery(symbol='AAPL', start=str(today-timedelta(days=90)), end=str(today+timedelta(days=1)))
tools = FinancialTools()
summary = []
for end, expected_rows in [('2026-10-06',65), ('2026-10-03',64)]:
    operation='analysis'
    query['end']=end
    result = json.loads(tools.call(operation, query))
    assert 'error' not in result, result
    assert result['period']['end_exclusive'] == end
    assert result['period']['last'] == ('2026-10-05' if expected_rows==65 else '2026-10-02'), result['period']
    if expected_rows==64:
        assert result['metrics']['sma200']['value'] is not None
    for metric in result['metrics'].values():
        assert metric['value'] is not None or metric['reason']
    assert result['indicator_history']['sample_size'] >= 200
    assert result['sample_size']==expected_rows
    summary.append({'operation': operation, 'period': result['period'],
                    'rows': len(result['rows']) if 'rows' in result else result['sample_size'],
                    'missing_adjusted_close_dates':[r['date'] for r in result['rows'] if r['adjusted_close'] is None], 'warnings':result['warnings'], 'source': result['source'], 'metrics': result['metrics'], 'indicator_history':result['indicator_history'], 'passed': True})
invalid = json.loads(tools.call('price_history', query | {'end': '2099-01-01'}))
assert invalid['error'] == 'invalid_financial_arguments' and 'tomorrow UTC' in invalid['hint']
print(json.dumps({'passed': True, 'queries': summary, 'date_error_guidance': True, 'paid_models_called': False}))
