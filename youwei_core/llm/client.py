"""LLM gateway client: the Core-side contract for every model call.

Budget semantics (S02 rules):
1. reserve a conservative max estimate BEFORE the request — when the
   budget ledger is unreachable this raises and NOTHING is sent
   (fail-closed)
2. settle the actual cost from the response usage after pricing
3. transport/gateway errors without usage -> release (no bookable
   cost). Known nuance deferred: partially streamed responses may have
   incurred upstream cost the error body does not report.
4. timeout/cancellation with unknown cost -> the reservation stays
   open and appears in pending reconciliation; it blocks that budget
   until reconciled or released
"""

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

import httpx

from youwei_core.budget.service import release, reserve, settle
from youwei_core.llm.pricing import (
    COST_CONFIRMED,
    DEFAULT_COST_MAP,
    estimate_max_cost_for,
    price_usage,
)


class GatewayError(Exception):
    pass


@dataclass
class LLMResult:
    content: str
    usage: dict
    raw: dict


class GatewayClient:
    def __init__(
        self,
        base_url: str,
        *,
        api_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 120.0,
        cost_map=DEFAULT_COST_MAP,
    ):
        self._cost_map = cost_map
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def chat(
        self,
        engine,
        *,
        run_id: uuid.UUID,
        attempt_id: uuid.UUID,
        call_seq: int,
        model: str,
        messages: list[dict],
        max_tokens: int,
        approx_input_tokens: int,
        tools: list[dict] | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> LLMResult:
        call_key = f"{attempt_id}:{call_seq}"

        # 1. fail-closed reservation before anything is sent
        estimate = estimate_max_cost_for(
            self._cost_map, model,
            max_tokens=max_tokens, approx_input_tokens=approx_input_tokens,
        )
        await reserve(
            engine,
            run_id,
            attempt_id=attempt_id,
            call_key=call_key,
            amount_micros=estimate,
        )

        payload: dict = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
        if extra:
            payload.update(extra)

        # 2. the upstream call
        try:
            resp = await self._http.post("/v1/chat/completions", json=payload)
        except (httpx.TimeoutException, asyncio.CancelledError):
            # unknown cost: keep the reservation, pending reconciliation
            raise
        except httpx.HTTPError as exc:
            await release(engine, run_id, call_key=call_key)
            raise GatewayError(f"gateway transport error: {exc}") from exc

        if resp.status_code != 200:
            await release(engine, run_id, call_key=call_key)
            raise GatewayError(
                f"gateway responded {resp.status_code}: {resp.text[:300]}"
            )

        data = resp.json()
        usage = data.get("usage") or {}

        # 3. settle the actual cost. A placeholder rate card prices usage as
        # an *estimate* (booked to estimated_micros, never confirmed); only a
        # reconciled card books to settled_micros.
        cost = price_usage(self._cost_map, model, usage)
        await settle(
            engine,
            run_id,
            attempt_id=attempt_id,
            call_key=call_key,
            actual_micros=cost.amount_micros,
            confirmed=(cost.status == COST_CONFIRMED),
        )

        content = ""
        choices = data.get("choices") or []
        if choices:
            content = choices[0].get("message", {}).get("content") or ""
        return LLMResult(content=content, usage=dict(usage), raw=data)
