"""Tenant HTTP boundary: daily data is PIT, never a live quote."""
import json
from datetime import date, timedelta

from test_data_pit import _ingest, _row, _security


async def test_daily_bars_cutoff_and_provenance(client, db_engine, tenant_headers):
    sec = await _security(db_engine)
    first = await _ingest(db_engine, {'AAPL': json.dumps([_row('2026-08-10', 100)])},
                          'AAPL', sec, date(2026, 8, 1), date(2026, 8, 31))
    query = dict(ticker='AAPL', start_date='2026-08-01', end_date='2026-08-31',
                 as_of=first.usable_at.isoformat())
    response = await client.get('/v1/data/daily-bars', params=query, headers=tenant_headers)
    assert response.status_code == 200
    body = response.json()
    assert body['frequency'] == 'daily'
    assert body['bars'][0]['close'] == 100
    assert body['bars'][0]['provenance']['source_id'] == 'tiingo'
    assert body['bars'][0]['provenance']['source_available_basis']
    assert 'quality' in body['bars'][0]
    await _ingest(db_engine, {'AAPL': json.dumps([_row('2026-08-10', 101)])},
                  'AAPL', sec, date(2026, 8, 1), date(2026, 8, 31))
    old = await client.get('/v1/data/daily-bars', params=query, headers=tenant_headers)
    assert old.json()['bars'] == body['bars']
    query['as_of'] = (first.usable_at - timedelta(seconds=1)).isoformat()
    assert (await client.get('/v1/data/daily-bars', params=query, headers=tenant_headers)).json()['bars'] == []
    del query['as_of']
    latest = (await client.get('/v1/data/daily-bars', params=query, headers=tenant_headers)).json()
    assert latest['bars'][0]['close'] == 101
    assert latest['as_of']


async def test_daily_bars_auth_validation_and_missing(client, db_engine, tenant_headers, admin_headers):
    await _security(db_engine)
    query = dict(ticker='AAPL', start_date='2026-08-01', end_date='2026-08-31')
    assert (await client.get('/v1/data/daily-bars', params=query)).status_code == 401
    assert (await client.get('/v1/data/daily-bars', params=query, headers=admin_headers)).status_code == 403
    empty = await client.get('/v1/data/daily-bars', params=query, headers=tenant_headers)
    assert empty.status_code == 200 and empty.json()['bars'] == []
    for patch in [dict(as_of='2099-01-01T00:00:00Z'), dict(as_of='2026-01-01T00:00:00'),
                  dict(start_date='2026-09-01'), dict(start_date='1900-01-01')]:
        assert (await client.get('/v1/data/daily-bars', params=query | patch, headers=tenant_headers)).status_code == 422
    assert (await client.get('/v1/data/daily-bars', params=query | dict(ticker='UNKNOWN'), headers=tenant_headers)).status_code == 404
