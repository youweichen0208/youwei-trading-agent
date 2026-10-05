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
for operation in ('price_history', 'indicators'):
    result = json.loads(tools.call(operation, query))
    assert 'error' not in result, result
    assert result['period']['end_exclusive'] == '2026-10-06'
    assert result['period']['last'] == '2026-10-05', result['period']
    summary.append({'operation': operation, 'period': result['period'],
                    'rows': len(result['rows']) if 'rows' in result else result['sample_size'],
                    'source': result['source'], 'passed': True})
invalid = json.loads(tools.call('price_history', query | {'end': '2099-01-01'}))
assert invalid['error'] == 'invalid_financial_arguments' and 'tomorrow UTC' in invalid['hint']
print(json.dumps({'passed': True, 'queries': summary, 'date_error_guidance': True, 'paid_models_called': False}))
