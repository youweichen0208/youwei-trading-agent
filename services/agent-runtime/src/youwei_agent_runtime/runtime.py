"""Hermes research runtime (S07): AIAgent invocation bridge.

This module wraps the pinned Hermes ``AIAgent`` (Python 3.14 checkout on SG)
behind a small, testable seam. It is the ONLY place that imports Hermes; the
rest of the package is pure logic (see adapter.py). Because Hermes is not a
pip dependency, importing this module requires the pinned checkout on
``sys.path`` — which only exists on the SG host (or inside the deployment
image). The import is therefore deferred so the package's pure-logic surface
still imports everywhere else.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from youwei_contracts.research import FrozenEvidence, ResearchProposal
from youwei_agent_runtime.adapter import ISOLATION_KWARGS, build_research_brief


class HermesNotAvailable(Exception):
    """Raised when the pinned Hermes checkout is not importable."""


@dataclass(frozen=True)
class ResearchConfig:
    """Gateway + model wiring for a research run. base_url must be the
    OpenAI-compatible gateway endpoint (scheme A); api_key is the gateway key,
    never a supplier credential. Cost attribution and budgeting live in the
    Controller/budget layer, not here."""

    base_url: str
    api_key: str
    model: str
    provider: str = "custom"
    max_iterations: int = 8
    run_budget_seconds: float | None = None


def _import_aiagent():
    try:
        from run_agent import AIAgent
    except ImportError as exc:  # pragma: no cover - only on hosts w/o Hermes
        raise HermesNotAvailable(
            "pinned Hermes checkout not on sys.path; run on SG host"
        ) from exc
    return AIAgent


def make_agent(config: ResearchConfig, *, platform: str = "research"):
    """Construct an isolated Hermes AIAgent for one research run.

    Applies ISOLATION_KWARGS (skip_memory / skip_context_files /
    skip_background_review / enabled_toolsets=[]) so Hermes has no built-in
    tools, no implicit memory, and no session search. Returns an agent whose
    chat() talks to the configured OpenAI-compatible gateway.
    """
    AIAgent = _import_aiagent()
    return AIAgent(
        provider=config.provider,
        base_url=config.base_url,
        api_key=config.api_key,
        model=config.model,
        platform=platform,
        max_iterations=config.max_iterations,
        run_budget_seconds=config.run_budget_seconds,
        **ISOLATION_KWARGS,
    )


def parse_proposal(raw: str, *, run_id, case_id) -> ResearchProposal:
    """Parse Hermes's textual answer into a validated ResearchProposal.

    The model is instructed (via the research brief) to return a JSON object
    matching the research-v1 proposal shape. This boundary enforces the wire
    discipline declared in the contract; the Controller re-validates at seal.
    """
    # Tolerate markdown fences / surrounding prose in a best-effort way.
    text = raw.strip()
    if text.startswith("```"):
        # strip a leading ```json ... ``` fence
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1 :]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[: text.rstrip().rfind("```")]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        # fall back to scanning for the first balanced {...} object
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("proposal response is not JSON: " + raw[:200])
        payload = json.loads(text[start : end + 1])

    from youwei_agent_runtime.adapter import proposal_from_payload

    return proposal_from_payload(run_id, case_id, payload)


async def run_research(
    evidence: FrozenEvidence, config: ResearchConfig
) -> ResearchProposal:
    """Run one research turn and return a validated ResearchProposal.

    This is the end-to-end seam used by the Controller (later slice): build
    the deterministic brief, run an isolated AIAgent turn against the gateway,
    and parse the answer into a proposal. Cost/budget/fencing are the
    Controller's concern; this function only produces the proposal value.
    """
    agent = make_agent(config)
    brief = build_research_brief(evidence)
    # chat() is synchronous; run it in a thread so the caller can cancel.
    import asyncio

    raw = await asyncio.to_thread(agent.chat, brief)
    return parse_proposal(raw, run_id=evidence.run_id, case_id=evidence.case.case_id)
