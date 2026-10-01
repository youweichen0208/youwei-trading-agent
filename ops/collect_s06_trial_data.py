"""Collect a frozen retrospective candidate dataset. No predictions are sealed.

Reads credentials from the environment (never prints them). A previously
frozen panel is required; this command never redraws it. Raw licensed data
belongs in the private output directory, never in Git.
"""
import argparse
import asyncio
import hashlib
import json
import os
import uuid
from datetime import UTC, date, datetime, time
from pathlib import Path

from sqlalchemy import select

from youwei_core.data.calendar import (
    ET, build_calendar, generate_calendar_days, timezone_provenance,
)
from youwei_core.data.panel import validate_panel_registration
from youwei_core.data.panel_bundle import export_registration_bundle
from youwei_core.data.securities import IdentitySpec, create_security, resolve_identifier
from youwei_core.data.tiingo import TiingoClient, ingest_daily, parse_daily_rows
from youwei_core.db.engine import make_engine
from youwei_core.db.meta import panel_registrations, raw_objects


def write_json(path, content):
    text = json.dumps(content, ensure_ascii=False, indent=2, default=str) + "\n"
    if path.exists():
        if path.read_text() != text:
            raise ValueError('refuse to overwrite frozen candidate file')
        return
    with path.open('x') as f:
        f.write(text)
    path.chmod(0o600)


async def collect(args):
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    if (out / 'historical-input.json').exists():
        raise ValueError('historical-input already frozen; reuse it rather than recollect')
    spec = json.loads(Path(args.spec).read_text())
    engine = make_engine(os.environ['YOUWEI_DATABASE_URL'])
    client = TiingoClient(os.environ['YOUWEI_TIINGO_TOKEN'], min_interval=1.1)
    try:
        registration_id = uuid.UUID(args.registration_id)
        validation = await validate_panel_registration(engine, registration_id)
        if not validation['ok']:
            raise ValueError('panel registration failed validation')
        bundle = await export_registration_bundle(engine, registration_id)
        write_json(out / 'panel-bundle.json', bundle)
        async with engine.connect() as conn:
            panel = (await conn.execute(select(panel_registrations).where(
                panel_registrations.c.id == registration_id))).mappings().one()
        observed_date = panel.frame_as_of
        spy = await resolve_identifier(engine, 'ticker', 'SPY', observed_date)
        if spy is None:
            spy = await create_security(engine, asset_class='etf', name='SPDR S&P 500 ETF Trust',
                identities=[IdentitySpec('ticker', 'SPY', observed_date)])
        build = await build_calendar(engine, year_start=2016, year_end=2028)
        days = generate_calendar_days(2016, 2028)
        sessions = [{
            'date': d['date'],
            'open_utc': datetime.combine(date.fromisoformat(d['date']), time(9, 30), ET).astimezone(UTC).isoformat(),
            'close_utc': datetime.combine(date.fromisoformat(d['date']), time(13 if d['early_close'] else 16), ET).astimezone(UTC).isoformat(),
        } for d in days if d['is_trading']]
        references = {
            'panel_registration_id': str(registration_id),
            'benchmark_security_id': str(spy), 'benchmark_ticker': 'SPY',
            'benchmark_identity_valid_from': observed_date.isoformat(),
            'benchmark_identity_basis': 'observed_current_SPY_daily_endpoint_not_historical_identity_proof',
            'calendar_version': build.version, 'calendar_sha256': build.content_sha256,
            'calendar_years': [2016, 2028], 'timezone': timezone_provenance(),
        }
        write_json(out / 'references.json', references)
        write_json(out / 'calendar.json', {'references': references, 'sessions': sessions, 'days': days})
        mapping = {r['security_id']: r['ticker'] for r in panel.mapping}
        selected = [(sid, mapping[sid]) for sid in panel.selected] + [(str(spy), 'SPY')]
        bars, sources, failures = {}, [], []
        for sid, ticker in selected:
            dest = out / f'prices-{sid}.json'
            if dest.exists():
                record = json.loads(dest.read_text())
                if record['ticker'] != ticker or record['query'] != [spec['data_start'], spec['data_end']]:
                    raise ValueError('existing frozen price object conflicts with trial')
            else:
                try:
                    ingested = await ingest_daily(engine, client, security_id=uuid.UUID(sid), ticker=ticker,
                        start_date=date.fromisoformat(spec['data_start']), end_date=date.fromisoformat(spec['data_end']))
                    async with engine.connect() as conn:
                        raw = (await conn.execute(select(raw_objects).where(raw_objects.c.id == ingested.raw_object_id))).mappings().one()
                    record = {'security_id': sid, 'ticker': ticker, 'query': [spec['data_start'], spec['data_end']],
                        'raw_object_id': str(raw.id), 'content': raw.content, 'content_sha256': raw.content_sha256,
                        'source_available_at': None, 'source_available_basis': raw.source_available_basis,
                        'ingested_at': raw.ingested_at.isoformat(), 'usable_at': raw.usable_at.isoformat()}
                    write_json(dest, record)
                except Exception as exc:
                    # No provider URLs, credential values or response bodies in logs.
                    failures.append({'security_id': sid, 'ticker': ticker, 'error_type': type(exc).__name__})
                    print(f'{ticker}: unavailable ({type(exc).__name__})', flush=True)
                    bars[sid] = []
                    continue
            if hashlib.sha256(record['content'].encode()).hexdigest() != record['content_sha256']:
                raise ValueError('frozen raw content hash mismatch')
            parsed = parse_daily_rows(record['content'])
            bars[sid] = [{**row, 'trade_date': row['trade_date'].isoformat(),
                'security_id': sid, 'quality': 'ok' if row['volume'] > 0 else 'zero_volume'} for row in parsed]
            sources.append({k: v for k, v in record.items() if k != 'content'})
            print(f'{ticker}: {len(parsed)} rows frozen', flush=True)
        dataset = {'dataset_version': 's06-retrospective-raw-v1', 'mode': 'historical_source',
            'pit_verified': False, 'scope': 'retrospective_current_panel', 'references': references,
            'panel_security_ids': panel.selected, 'sources': sources, 'failures': failures,
            'bars_by_security': bars, 'calendar_sessions': sessions,
            'collected_at': datetime.now(UTC).isoformat()}
        # Write once. Reruns may finish missing downloads, but must not overwrite
        # a frozen dataset which could already have been used by a trial.
        final = out / 'historical-input.json'
        if final.exists():
            raise ValueError('historical-input already frozen; reuse it or register a new input')
        write_json(final, dataset)
        print(json.dumps({'rows': sum(map(len, bars.values())), 'failed_sources': len(failures),
            'input_sha256': hashlib.sha256(final.read_bytes()).hexdigest()}))
    finally:
        await client.aclose()
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registration-id', required=True)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--spec', default='docs/trials/trial-001-spec.v1.json')
    asyncio.run(collect(parser.parse_args()))


if __name__ == '__main__':
    main()
