import asyncio
import hashlib
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr
from yta_alpha.errors import DomainError
from yta_alpha.massive import MassiveCandidate, decode_payload

TOKEN = "fixture-provider-credential-not-real"
START, END = date(2024, 1, 1), date(2026, 1, 1)
BAR_PATH = "/v2/aggs/ticker/NVDA/range/1/day/2024-01-01/2026-01-01"
NOW = datetime(2026, 10, 27, tzinfo=UTC)


def candidate(handler):
    return MassiveCandidate(
        SecretStr(TOKEN), transport=httpx.MockTransport(handler), clock=lambda: NOW
    )


def test_exact_decimal_raw_evidence_and_both_current_endpoints():
    calls = []
    raw = (
        b'{"status":"OK","ticker":"NVDA","adjusted":false,"results":'
        b'[{"o":123,"h":124,"l":122,"c":123.123456789123456789,"v":100,"t":1704153600000}]}'
    )

    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer " + TOKEN
        assert TOKEN not in str(request.url)
        if request.url.path == BAR_PATH:
            assert dict(request.url.params) == {
                "adjusted": "false",
                "sort": "asc",
                "limit": "50000",
            }
            return httpx.Response(200, content=raw, headers={"content-type": "application/json"})
        assert request.url.path == "/stocks/v1/splits"
        assert request.url.params["ticker"] == "NVDA"
        return httpx.Response(200, json={"status": "OK", "results": []})

    result = asyncio.run(candidate(handler).acquire(START, END))
    assert result.qualification_status == "UNVERIFIED"
    assert len(calls) == 2 and len(result.bars) == len(result.splits) == 1
    page = result.bars[0]
    assert page.body == raw and page.content_hash == hashlib.sha256(raw).hexdigest()
    assert page.retrieved_at == NOW
    assert page.payload()["results"][0]["c"] == Decimal("123.123456789123456789")
    assert type(page.payload()["results"][0]["v"]) is int
    changed = page.payload()
    changed["results"].clear()
    assert page.payload()["results"]


@pytest.mark.parametrize("payload", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b"[]"])
def test_ambiguous_or_nonfinite_json_rejected(payload):
    with pytest.raises(ValueError):
        decode_payload(payload)


@pytest.mark.parametrize(
    "next_url",
    [
        "https://attacker.example/path?cursor=x",
        "http://api.massive.com" + BAR_PATH + "?cursor=x",
        "https://user:pass@api.massive.com" + BAR_PATH + "?cursor=x",
        "https://api.massive.com/other?cursor=x",
        "https://api.massive.com" + BAR_PATH + "?cursor=x&apiKey=secret",
        "https://api.massive.com" + BAR_PATH + "?cursor=x&cursor=y",
        "https://api.massive.com" + BAR_PATH + "?cursor=x&adjusted=true",
    ],
)
def test_pagination_cannot_escape_origin_or_scope(next_url):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "status": "OK",
                "ticker": "NVDA",
                "adjusted": False,
                "results": [],
                "next_url": next_url,
            },
        )

    with pytest.raises(DomainError) as error:
        asyncio.run(candidate(handler).acquire(START, END))
    assert error.value.code == "INVALID_PROVIDER_RESPONSE"
    assert len(calls) == 1 and TOKEN not in str(error.value)


def test_valid_pagination_and_gaps_are_preserved():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == BAR_PATH:
            result = {"status": "OK", "ticker": "NVDA", "adjusted": False, "results": []}
            if "cursor" not in request.url.params:
                result["next_url"] = "https://api.massive.com" + BAR_PATH + "?cursor=page2"
            return httpx.Response(200, json=result)
        return httpx.Response(200, json={"status": "OK", "results": []})

    result = asyncio.run(candidate(handler).acquire(START, END))
    assert len(calls) == 3 and len(result.bars) == 2
    assert all(page.payload()["results"] == [] for page in result.bars)


@pytest.mark.parametrize(
    "status,code,retryable",
    [
        (401, "PROVIDER_AUTH_FAILED", False),
        (403, "PROVIDER_ACCESS_DENIED", False),
        (429, "PROVIDER_RATE_LIMITED", True),
        (500, "PROVIDER_UNAVAILABLE", True),
        (302, "PROVIDER_UNAVAILABLE", False),
    ],
)
def test_provider_failures_are_redacted_without_fallback(status, code, retryable):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text=TOKEN, headers={"location": "https://attacker.example"})

    with pytest.raises(DomainError) as error:
        asyncio.run(candidate(handler).acquire(START, END))
    assert error.value.code == code and error.value.retryable is retryable
    assert TOKEN not in str(error.value) and len(calls) == 1


def test_pagination_cycle_is_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "status": "OK",
                "ticker": "NVDA",
                "adjusted": False,
                "results": [],
                "next_url": "https://api.massive.com" + BAR_PATH + "?cursor=repeat",
            },
        )

    with pytest.raises(DomainError):
        asyncio.run(candidate(handler).acquire(START, END))
    assert len(calls) == 2


def test_adjusted_data_is_rejected():
    with pytest.raises(DomainError) as error:
        asyncio.run(
            candidate(
                lambda request: httpx.Response(
                    200, json={"status": "OK", "ticker": "NVDA", "adjusted": True, "results": []}
                )
            ).acquire(START, END)
        )
    assert error.value.code == "INVALID_PROVIDER_RESPONSE"


@pytest.mark.parametrize("extra", [0, 1])
def test_exact_response_byte_boundary(monkeypatch, extra):
    import yta_alpha.massive as module

    monkeypatch.setattr(module, "MAX_PAGE_BYTES", 256)
    raw = b'{"status":"OK","ticker":"NVDA","adjusted":false,"results":[]}'
    raw += b" " * (256 - len(raw) + extra)

    def handler(request):
        if request.url.path == BAR_PATH:
            return httpx.Response(200, content=raw, headers={"content-type": "application/json"})
        return httpx.Response(200, json={"status": "OK", "results": []})

    if extra:
        with pytest.raises(DomainError):
            asyncio.run(candidate(handler).acquire(START, END))
    else:
        assert len(asyncio.run(candidate(handler).acquire(START, END)).bars[0].body) == 256


def test_unique_pagination_is_still_bounded(monkeypatch):
    import yta_alpha.massive as module

    monkeypatch.setattr(module, "MAX_PAGES", 2)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "status": "OK",
                "ticker": "NVDA",
                "adjusted": False,
                "results": [],
                "next_url": "https://api.massive.com" + BAR_PATH + f"?cursor={len(calls)}",
            },
        )

    with pytest.raises(DomainError):
        asyncio.run(candidate(handler).acquire(START, END))
    assert len(calls) == 2


def test_acquisition_total_deadline(monkeypatch):
    import yta_alpha.massive as module

    monkeypatch.setattr(module, "ACQUISITION_TIMEOUT", 0.01)

    async def handler(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json={})

    with pytest.raises(DomainError) as error:
        asyncio.run(candidate(handler).acquire(START, END))
    assert error.value.code == "PROVIDER_UNAVAILABLE" and error.value.retryable


@pytest.mark.parametrize(
    "change",
    [
        {"o": True},
        {"c": "123"},
        {"h": None},
        {"l": 0},
        {"v": -1},
        {"c": 126},
        {"o": 120},
        {"t": True},
        {"t": 1704153600000.5},
        {"t": 10**30},
        {"t": 0},
    ],
)
def test_invalid_bar_rejected_before_split_request(change):
    calls = []
    row = {"o": 123, "h": 125, "l": 122, "c": 124, "v": 0, "t": 1704171600000}
    row.update(change)

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "status": "OK",
                "ticker": "NVDA",
                "adjusted": False,
                "results": [row],
            },
        )

    with pytest.raises(DomainError) as error:
        asyncio.run(candidate(handler).acquire(START, END))
    assert error.value.code == "INVALID_PROVIDER_RESPONSE"
    assert len(calls) == 1


def test_daily_dates_use_eastern_time_and_keep_gaps_across_dst():
    # Local midnight before/after the DST transition is 05:00 / 04:00 UTC.
    timestamps = [1709874000000, 1710129600000]  # 2024-03-08 / 2024-03-11
    rows = [dict(o=100, h=102, l=99, c=101, v=0, t=t) for t in timestamps]

    def handler(request):
        if request.url.path.startswith("/v2/aggs/"):
            return httpx.Response(
                200,
                json={
                    "status": "OK",
                    "ticker": "NVDA",
                    "adjusted": False,
                    "results": rows,
                },
            )
        return httpx.Response(200, json={"status": "OK", "results": []})

    result = asyncio.run(candidate(handler).acquire(date(2024, 3, 8), date(2024, 3, 11)))
    assert result.bars[0].payload()["results"] == rows
    # UTC March 8 at 00:00 is still March 7 in New York, outside the request.
    rows[0]["t"] = 1709856000000
    with pytest.raises(DomainError):
        asyncio.run(candidate(handler).acquire(date(2024, 3, 8), date(2024, 3, 11)))


@pytest.mark.parametrize("second_timestamp", [1704171600000, 1704175200000, 1704085200000])
def test_cross_page_duplicate_daily_dates_and_reversed_order_rejected(second_timestamp):
    calls = []

    def handler(request):
        calls.append(request)
        timestamp = 1704171600000 if len(calls) == 1 else second_timestamp
        payload = {
            "status": "OK",
            "ticker": "NVDA",
            "adjusted": False,
            "results": [dict(o=100, h=102, l=99, c=101, v=1, t=timestamp)],
        }
        if len(calls) == 1:
            payload["next_url"] = "https://api.massive.com" + BAR_PATH + "?cursor=page2"
        return httpx.Response(200, json=payload)

    with pytest.raises(DomainError):
        asyncio.run(candidate(handler).acquire(START, END))
    assert len(calls) == 2


def split_candidate(rows):
    def handler(request):
        if request.url.path == BAR_PATH:
            return httpx.Response(
                200,
                json={
                    "status": "OK",
                    "ticker": "NVDA",
                    "adjusted": False,
                    "results": [],
                },
            )
        return httpx.Response(200, json={"status": "OK", "results": rows})

    return candidate(handler)


def split_row(**changes):
    return dict(
        {
            "id": "fixture-split-1",
            "ticker": "NVDA",
            "execution_date": "2024-06-10",
            "split_from": 1,
            "split_to": 10,
            "adjustment_type": "forward_split",
            "historical_adjustment_factor": 999,
        },
        **changes,
    )


def test_split_normalization_preserves_ratios_and_conservative_availability():
    rows = [
        split_row(),
        split_row(
            id="fixture-split-2",
            execution_date="2025-06-10",
            split_from=10,
            split_to=1,
            adjustment_type="reverse_split",
        ),
    ]
    result = asyncio.run(split_candidate(rows).acquire(START, END))
    assert result.qualification_status == "UNVERIFIED"
    first, second = result.split_events
    assert (first.split_from, first.split_to) == (Decimal(1), Decimal(10))
    assert (second.split_from, second.split_to) == (Decimal(10), Decimal(1))
    assert first.effective_date == date(2024, 6, 10)
    assert first.available_at == second.available_at == NOW
    assert first.raw_page_hash == second.raw_page_hash == result.splits[0].content_hash
    assert result.splits[0].payload()["results"] == rows
    assert not hasattr(result, "actions_verified_through")


@pytest.mark.parametrize(
    "changes",
    [
        {"ticker": "AAPL"},
        {"id": None},
        {"id": " "},
        {"execution_date": "20240610"},
        {"execution_date": "2024-02-30"},
        {"execution_date": "2023-06-10"},
        {"execution_date": None},
        {"split_from": None},
        {"split_from": True},
        {"split_to": "10"},
        {"split_to": 0},
        {"split_from": -1},
        {"split_to": 1},
        {"adjustment_type": "reverse_split"},
    ],
)
def test_malformed_split_is_rejected(changes):
    with pytest.raises(DomainError) as error:
        asyncio.run(split_candidate([split_row(**changes)]).acquire(START, END))
    assert error.value.code == "INVALID_PROVIDER_RESPONSE"


@pytest.mark.parametrize("kind", ["stock_dividend", "unknown", None])
def test_unsupported_action_is_not_silently_omitted(kind):
    with pytest.raises(DomainError) as error:
        asyncio.run(split_candidate([split_row(adjustment_type=kind)]).acquire(START, END))
    assert error.value.code == "UNSUPPORTED_CORPORATE_ACTION"


@pytest.mark.parametrize(
    "second",
    [
        split_row(execution_date="2025-06-10"),
        split_row(id="different-id"),
        split_row(id="different-id", execution_date="2024-01-10"),
    ],
)
def test_duplicate_identity_same_day_and_reversed_split_events_rejected(second):
    with pytest.raises(DomainError):
        asyncio.run(split_candidate([split_row(), second]).acquire(START, END))


def test_empty_split_result_does_not_attest_coverage():
    result = asyncio.run(split_candidate([]).acquire(START, END))
    assert result.split_events == () and result.qualification_status == "UNVERIFIED"
    assert not hasattr(result, "actions_verified_through")


def test_split_precision_and_cross_page_identity_validation():
    from yta_alpha.massive import RawPage, normalize_splits

    raw = (
        b'{"results":[{"id":"exact","ticker":"NVDA","execution_date":"2024-06-10",'
        b'"split_from":1.000000000000000001,"split_to":1.000000000000000002,'
        b'"adjustment_type":"forward_split"}]}'
    )
    first = RawPage("/stocks/v1/splits", (), NOW, raw)
    event = normalize_splits([first], START, END)[0]
    assert event.split_from == Decimal("1.000000000000000001")
    assert event.split_to == Decimal("1.000000000000000002")
    second = RawPage("/stocks/v1/splits", (("cursor", "two"),), NOW, raw)
    with pytest.raises(ValueError):
        normalize_splits([first, second], START, END)
