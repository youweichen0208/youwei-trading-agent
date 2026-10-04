"""
Title: youwei 研究助手
Author: youwei
Description: 发起探索性美股研究（基于冻结证据，独立于正式预测 Ledger）。用法：研究 AAPL D20 / 状态 <id> / 取消 <id>
Version: 0.1.0

Open WebUI Pipe Function (verified against the pinned v0.6.36 container
semantics, 2026-10-03):

- a module-level ``Pipe`` class; active functions surface as a selectable
  "model" in the chat UI
- ``Valves`` (pydantic) + ``self.valves`` — admin-editable in the UI and
  persisted by Open WebUI; the Core API key lives SERVER-SIDE only
- the runtime injects parameters by signature name: ``body`` (the OpenAI
  chat form data) and ``__user__`` (the chatting user dict); ``pipe`` may
  be async and a plain ``str`` return becomes the chat message

This adapter is deliberately THIN: submit / poll / summarize / cancel.
Task state lives in Core (refresh or reconnect loses nothing); research
itself runs in Core's controlled runtime. It never orchestrates research
itself and never talks to anything but the Core API.

Semantics:
- "研究 <TICKER> [D1|D20|D60]" submits one exploratory research question.
  The idempotency key binds (Open WebUI user, message text): resending
  the same text returns the CURRENT state of that research instead of
  silently starting a duplicate — ask a differently-worded question (or
  use a different horizon) to start a fresh one.
- The pipe polls up to ``poll_timeout_seconds`` and then returns an
  interim message with the research id (query later with 状态/取消).
- "状态 <research_id>" / "取消 <research_id>" address existing tasks.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from typing import Any

import httpx
from pydantic import BaseModel, Field

USAGE = (
    "用法：\n"
    "- 研究 <代码> [D1|D20|D60] —— 发起探索性研究（默认 D20）\n"
    "- 状态 <research_id> —— 查询任务状态\n"
    "- 取消 <research_id> —— 取消任务\n"
    "研究基于提交时刻冻结的行情证据，结果独立于正式预测评分。"
)

_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,15}$")
_HORIZON_RE = re.compile(r"\bD(1|20|60)\b")
_STATUS_RE = re.compile(r"^(?:状态|status)\s+([0-9a-f-]{36})\s*$", re.IGNORECASE)
_CANCEL_RE = re.compile(r"^(?:取消|cancel)\s+([0-9a-f-]{36})\s*$", re.IGNORECASE)
_RESEARCH_RE = re.compile(
    r"^(?:研究|research|分析)\s+([A-Za-z][A-Za-z0-9.\-]{0,15})"
    r"(?:\s+D(1|20|60))?\s*$",
    re.IGNORECASE,
)

TERMINAL = ("succeeded", "failed", "cancelled")


class Valves(BaseModel):
    core_url: str = Field(
        default="http://youwei-production-core-api-1:8000",
        description="Core API 基址（openwebui 容器经 edge 网络访问）",
    )
    core_api_key: str = Field(default="", description="Core tenant API key（仅服务端）")
    dashboard_base_url: str = Field(
        default="https://dash.youwei-agent.com",
        description="报告详情链接基址（Dashboard 研究页）",
    )
    default_horizon: int = Field(default=20, description="未指定时的窗口（1/20/60）")
    poll_interval_seconds: float = Field(default=3.0, ge=0.1)
    poll_timeout_seconds: float = Field(default=90.0, ge=0.5)


class Pipe:
    class Valves(Valves):
        pass

    def __init__(self):
        self.valves = self.Valves()

    # --- message parsing (pure; tested offline) --------------------------

    @staticmethod
    def parse_message(text: str, default_horizon: int) -> dict:
        """Classify the user message into an action; raises ValueError on
        an unparsable research request (e.g. malformed ticker)."""
        text = (text or "").strip()
        if not text:
            return {"action": "help"}
        m = _STATUS_RE.match(text)
        if m:
            return {"action": "status", "research_id": m.group(1).lower()}
        m = _CANCEL_RE.match(text)
        if m:
            return {"action": "cancel", "research_id": m.group(1).lower()}
        m = _RESEARCH_RE.match(text)
        if m:
            ticker = m.group(1).upper()
            if not _TICKER_RE.match(ticker):
                raise ValueError(f"无法识别的代码：{ticker}")
            horizon = int(m.group(2)) if m.group(2) else default_horizon
            return {"action": "research", "ticker": ticker, "horizon_td": horizon}
        if _HORIZON_RE.search(text) or re.search(r"\b[A-Z]{1,6}\b", text.upper()):
            # looks like an attempt at a research request but not well-formed
            raise ValueError("无法解析研究请求；示例：研究 AAPL D20")
        return {"action": "help"}

    @staticmethod
    def idempotency_key(user: dict | None, text: str) -> str:
        """Same user + same message -> same research (resend shows the
        current state instead of duplicating the work)."""
        uid = (user or {}).get("id", "anonymous")
        digest = hashlib.sha256(text.strip().encode()).hexdigest()[:16]
        return f"owui:{uid}:{digest}"

    # --- rendering (pure; tested offline) ---------------------------------

    def render_task(self, task: dict) -> str:
        status = task.get("status", "?")
        lines = [
            f"研究 {task.get('ticker', '?')} vs {task.get('benchmark_ticker', '?')} "
            f"D{task.get('horizon_td', '?')} —— {status}"
        ]
        if status == "succeeded":
            lines.append(self._detail_line(task["research_id"]))
        elif status in ("failed", "cancelled"):
            lines.append(f"research_id：{task['research_id']}")
        return "\n".join(lines)

    def render_report(self, report: dict) -> str:
        c = report.get("content", {})
        s = c.get("summary", {})
        quant = c.get("quant", {}).get("quant_model", {})
        ce = c.get("counter_evidence", {})
        lines = []
        if s.get("source_status") == "produced":
            relation = s.get("quant_relation")
            lines.append(
                f"结论：p_outperform={s.get('p_outperform')}，"
                f"预期超额={s.get('expected_excess_return')}"
                + (f"（相对量化预测：{relation}）" if relation else "")
            )
        else:
            lines.append(f"研究未给出结论：{s.get('basis') or '证据不足'}")
        if quant.get("source_status") == "produced":
            lines.append(
                f"量化输入：p={quant.get('p_outperform')}（{quant.get('model_version')}）"
            )
        else:
            lines.append(f"量化输入：unavailable（{quant.get('reason')}）")
        if ce.get("quantitative_basis"):
            lines.append(f"依据：{ce['quantitative_basis']}")
        for w in ce.get("warnings", []) or []:
            lines.append(f"警告：{w.get('kind')} —— {w.get('detail')}")
        if ce.get("missing"):
            lines.append(f"未能落实：{'、'.join(ce['missing'])}")
        lims = c.get("limitations", []) or []
        if lims:
            lines.append("限制：")
            lines.extend(f"- {lim}" for lim in lims[:5])
        lines.append(
            f"报告 v{report.get('report_version')}"
            + ("（非最新）" if not report.get("is_latest") else "")
        )
        lines.append(self._detail_line(report.get("research_id", "")))
        return "\n".join(lines)

    def _detail_line(self, research_id: str) -> str:
        return (
            f"research_id：{research_id}\n"
            f"详情：{self.valves.dashboard_base_url}/#/research/{research_id}"
        )

    # --- Core API (thin client; transport injectable for tests) ----------

    async def pipe(
        self,
        body: dict,
        __user__: dict | None = None,
        __task__: str | None = None,
        _client: httpx.AsyncClient | None = None,
    ) -> str:
        # Open WebUI background tasks (title/tag/follow-up generation) also
        # route through the selected model — they must NEVER trigger a
        # research submission or consume a Core call. The pinned v0.6.36
        # injects ``__task__`` (metadata.task) for those requests; echo a
        # short title derived from the first user message instead.
        if __task__:
            messages = body.get("messages") or []
            for m in messages:
                if m.get("role") == "user":
                    text = (m.get("content") or "").strip().replace("\n", " ")
                    return text[:48] or "研究对话"
            return "研究对话"
        text = _last_user_message(body)
        client = _client or httpx.AsyncClient(
            base_url=self.valves.core_url, timeout=15.0
        )
        try:
            return await self._dispatch(text, __user__, client)
        finally:
            if _client is None:
                await client.aclose()

    async def _dispatch(self, text: str, user: dict | None, client: httpx.AsyncClient) -> str:
        try:
            parsed = self.parse_message(text, self.valves.default_horizon)
        except ValueError as exc:
            return f"{exc}\n\n{USAGE}"

        action = parsed["action"]
        if action == "help":
            return USAGE
        try:
            if action == "status":
                task = await self._get_task(client, parsed["research_id"])
                return self.render_task(task)
            if action == "cancel":
                return await self._cancel(client, parsed["research_id"])

            # research
            task = await self._submit(
                client, parsed["ticker"], parsed["horizon_td"], user, text
            )
        except CoreError as exc:
            return f"请求失败：{exc}"
        except RuntimeError as exc:
            return f"配置错误：{exc}"
        return await self._await_terminal(client, task)

    async def _submit(
        self,
        client: httpx.AsyncClient,
        ticker: str,
        horizon_td: int,
        user: dict | None,
        text: str,
    ) -> dict:
        r = await client.post(
            "/v1/research",
            json={"ticker": ticker, "horizon_td": horizon_td},
            headers={
                "Authorization": f"Bearer {self._require_key()}",
                "Idempotency-Key": self.idempotency_key(user, text),
            },
        )
        if r.status_code in (200, 201):
            return r.json()
        raise CoreError(self._explain(r))

    async def _get_task(self, client: httpx.AsyncClient, research_id: str) -> dict:
        r = await client.get(
            f"/v1/research/{research_id}",
            headers={"Authorization": f"Bearer {self._require_key()}"},
        )
        if r.status_code != 200:
            raise CoreError(self._explain(r))
        return r.json()

    async def _cancel(self, client: httpx.AsyncClient, research_id: str) -> str:
        r = await client.post(
            f"/v1/research/{research_id}/cancel",
            headers={"Authorization": f"Bearer {self._require_key()}"},
        )
        if r.status_code != 200:
            raise CoreError(self._explain(r))
        return f"已请求取消：{r.json().get('status')}（research_id：{research_id}）"

    async def _await_terminal(self, client: httpx.AsyncClient, task: dict) -> str:
        research_id = task["research_id"]
        waited = 0.0
        while True:
            if task.get("status") in TERMINAL:
                break
            if waited >= self.valves.poll_timeout_seconds:
                return (
                    f"研究仍在执行（状态：{task.get('status')}），已等待 {int(waited)}s。\n"
                    f"稍后发送：状态 {research_id}\n" + self._detail_line(research_id)
                )
            await asyncio.sleep(self.valves.poll_interval_seconds)
            waited += self.valves.poll_interval_seconds
            task = await self._get_task(client, research_id)

        if task["status"] == "succeeded":
            r = await client.get(
                f"/v1/research/{research_id}/report",
                headers={"Authorization": f"Bearer {self._require_key()}"},
            )
            if r.status_code == 200:
                return self.render_report(r.json())
            return f"研究完成，但报告读取失败（{r.status_code}）。\n" + self._detail_line(research_id)
        return f"研究未完成：{task['status']}\nresearch_id：{research_id}"

    def _require_key(self) -> str:
        key = self.valves.core_api_key
        if not key:
            # env fallback (container env), valves take precedence
            import os

            key = os.environ.get("YOUWEI_CORE_KEY", "")
        if not key:
            raise RuntimeError("Core API key 未配置")
        return key

    @staticmethod
    def _explain(r: httpx.Response) -> str:
        try:
            detail = r.json().get("detail") or r.json()
        except Exception:
            detail = r.text[:200]
        if r.status_code in (401, 403):
            return "认证失败（Core API key 无效或权限不足）"
        if r.status_code == 404:
            return "research 未找到（检查 research_id 或租户归属）"
        if r.status_code == 409:
            return "幂等冲突：同键不同请求"
        if r.status_code == 422:
            return f"请求被拒绝：{detail}"
        return f"Core 返回 {r.status_code}：{detail}"


class CoreError(Exception):
    pass


def _last_user_message(body: dict) -> str:
    """The newest user message text (OpenAI chat shape; files/citations
    attached as dicts are skipped)."""
    for message in reversed(body.get("messages", []) or []):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            texts = [
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            joined = "\n".join(t for t in texts if t.strip())
            if joined:
                return joined
    return ""
