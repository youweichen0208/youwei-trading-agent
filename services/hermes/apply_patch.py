"""Apply the Alpha runtime seam only to the audited immutable Hermes source.

Usage: python services/hermes/apply_patch.py /path/to/hermes
All inputs are validated before any output is written. The deployed image must
include alpha_hermes on PYTHONPATH; importing the patched core otherwise fails.
"""

import ast
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent


def replace_once(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise ValueError("patch_anchor_mismatch")
    return source.replace(old, new, 1)


def patched_sources(root: Path) -> dict[Path, str]:
    pin = json.loads((ROOT / "upstream.json").read_text())
    sources = {}
    # The patch relies on canonical admission/recovery and durable SQLite
    # behavior outside the edited files. Validate that same full source
    # boundary in image builds as in the real-process integration tests.
    for group in ("files", "auditFiles", "runtimeFiles"):
        for name, digest in pin[group].items():
            # GitHub's text transport can add a trailing newline; no other
            # source normalization is allowed by this pin.
            source = (root / name).read_text().rstrip("\n") + "\n"
            if hashlib.sha256(source.encode()).hexdigest() != digest:
                raise ValueError("upstream_source_mismatch: " + name)
            if group == "files":
                sources[name] = source

    name = "gateway/platforms/api_server_runs.py"
    source = sources[name]
    source = replace_once(source,
        '    body, room_error = await self._normalize_room_dispatch(request, body)\n',
        '''    from alpha_hermes.bridge import (
        BridgeError, validate_request, fingerprint, bind_admission)
    try:
        if not self._run_idempotency_store.durable:
            raise BridgeError("durable_idempotency_required")
        alpha_capability = validate_request(
            body, request.headers,
            getattr(self.gateway_runner, 'session_authority', None) is not None)
    except BridgeError as exc:
        return _json_error(_openai_error, str(exc), status=400)
    body, room_error = await self._normalize_room_dispatch(request, body)
''')
    source = replace_once(source,
        '        self, request, body, gateway_session_key, idempotency_key)\n',
        '''        self, request, body, gateway_session_key, idempotency_key)
    idempotency_fingerprint = fingerprint(idempotency_fingerprint, alpha_capability)
''')
    source = replace_once(source,
        '    q = self._run_streams[run_id] = _RunStream()\n',
        '''    # Persist authorization before a durable admission can execute.
    bind_admission(run_id, session_id, alpha_capability)
    q = self._run_streams[run_id] = _RunStream()
''')
    source = replace_once(source,
        '    admitted = await self._admit_to_live_bot_chat(session_id, user_message, turn_author) '
        'if selected_session_id else None\n',
        '    admitted = None  # Alpha Profiles never hand work to a Desktop owner.\n')
    # Expose the provider's native reasoning to the run SSE. The canonical turn
    # already publishes ``reasoning_callback`` to observers; the runs adapter
    # simply never registered a sink. Reasoning is buffered per tool boundary so
    # the stream carries one bounded thinking block per call instead of tokens.
    source = replace_once(source,
        '''        with suppress(Exception):
            loop.call_soon_threadsafe(run.put_event, _run_event(
                run_id, "message.interim", text=text, already_streamed=bool(already_streamed)))

    def _finish(status: str, extra: Optional[dict] = None, **fields: Any) -> None:
''',
        '''        with suppress(Exception):
            loop.call_soon_threadsafe(run.put_event, _run_event(
                run_id, "message.interim", text=text, already_streamed=bool(already_streamed)))

    _reasoning_parts: list[str] = []

    def _flush_reasoning() -> None:
        text = "".join(_reasoning_parts).strip()
        _reasoning_parts.clear()
        if not text or run_id not in self._run_streams:
            return
        text = _redact_api_error_text(text)
        if len(text) > 2000:
            text = "…" + text[-1999:]
        with suppress(Exception):
            loop.call_soon_threadsafe(run.put_event, _run_event(
                run_id, "reasoning.delta", text=text))

    def _reasoning_cb(text: str) -> None:
        # Native provider reasoning, bounded before it reaches the public run
        # stream. It is never mixed into the final answer text.
        if not isinstance(text, str) or not text:
            return
        _reasoning_parts.append(text)
        if sum(len(part) for part in _reasoning_parts) > 6000:
            _flush_reasoning()

    def _finish(status: str, extra: Optional[dict] = None, **fields: Any) -> None:
''')
    source = replace_once(source,
        '''                result, usage = await observe_api_turn(
                    run.admission, stream_delta_callback=_text_cb,
                    tool_start_callback=_tool_start, tool_complete_callback=_tool_complete)
''',
        '''                result, usage = await observe_api_turn(
                    run.admission, stream_delta_callback=_text_cb,
                    tool_start_callback=_tool_start, tool_complete_callback=_tool_complete,
                    reasoning_callback=_reasoning_cb)
''')
    source = replace_once(source,
        '''            def _tool_start(call_id, name, args):
                from agent.display import build_tool_preview
                tool_cb("tool.started", name, build_tool_preview(name, args or {}) or "", args, tool_call_id=call_id)
''',
        '''            def _tool_start(call_id, name, args):
                _flush_reasoning()
                from agent.display import build_tool_preview
                tool_cb("tool.started", name, build_tool_preview(name, args or {}) or "", args, tool_call_id=call_id)
''')
    source = replace_once(source,
        '''            with self._profile_scope(run.request_profile):
                agent = self._create_agent(
                    stream_delta_callback=_text_cb, tool_progress_callback=self._make_run_event_callback(run_id, loop),
                    interim_assistant_callback=_interim_cb, **run.agent_kwargs)
''',
        '''            with self._profile_scope(run.request_profile):
                _run_tool_cb = self._make_run_event_callback(run_id, loop)

                def _tool_progress(event_type, tool_name=None, preview=None, args=None, **kwargs):
                    if event_type == "tool.started":
                        _flush_reasoning()
                    return _run_tool_cb(event_type, tool_name, preview, args, **kwargs)

                agent = self._create_agent(
                    stream_delta_callback=_text_cb, tool_progress_callback=_tool_progress,
                    interim_assistant_callback=_interim_cb, reasoning_callback=_reasoning_cb,
                    **run.agent_kwargs)
''')
    source = replace_once(source,
        '''        # Publish request metrics (daily counters + latency) with each completed run (#52323).
        self._record_api_metrics(usage, time.perf_counter() - _run_started_at)
''',
        '''        _flush_reasoning()
        # Publish request metrics (daily counters + latency) with each completed run (#52323).
        self._record_api_metrics(usage, time.perf_counter() - _run_started_at)
''')
    sources[name] = source

    name = "gateway/session_api_turn.py"
    source = sources[name]
    source = replace_once(source,
        "                         'relay_metadata', 'resume_unanswered'}",
        "                         'relay_metadata', 'resume_unanswered', 'alpha_run_id'}")
    source = replace_once(source,
        "    run_owner_scope = kwargs.get('run_owner_scope')\n",
        '''    from alpha_hermes.bridge import admission_identity
    alpha_run_id = admission_identity(kwargs.get('active_run_id'))
    if alpha_run_id:
        payload['api_turn_v1']['alpha_run_id'] = alpha_run_id
    run_owner_scope = kwargs.get('run_owner_scope')
''')
    source = replace_once(source,
        "    return {'adapter': adapter, 'settings': settings, "
        "'history': data['history'] if data else None,\n",
        '''    return {'adapter': adapter, 'settings': settings,
            'history': data['history'] if data else None,
            'alpha_run_id': data.get('alpha_run_id') if data else None,
''')
    sources[name] = source

    name = "agent/tool_executor.py"
    source = sources[name]
    source = replace_once(source,
        "def _run_agent_tool_execution_middleware(\n",
        "from alpha_hermes.bridge import guard_dispatch as _alpha_guard_dispatch\n\n\n"
        "@_alpha_guard_dispatch\ndef _run_agent_tool_execution_middleware(\n")
    # Reject before inline execution and deferred tool-search unwrapping. A
    # hallucinated built-in tool must never open a terminal or read private files.
    source = replace_once(source,
        '    name = _canonical_tool_name(tool_call.function.name)\n',
        '''    from alpha_hermes.bridge import role_tools
    name = _canonical_tool_name(tool_call.function.name)
    if name not in {tool['name'] for tool in role_tools()}:
        return _ParsedCall(tool_call, name, {}, [], 'Alpha tool not allowed', None)
''')
    sources[name] = source
    name = "agent/anthropic_adapter.py"
    sources[name] = replace_once(sources[name],
        '    style = _auth_style(api_key, base_url, normalized_base_url)\n',
        '''    from alpha_hermes.model_transport import anthropic_auth_style
    style = anthropic_auth_style(
        api_key, base_url, _auth_style(api_key, base_url, normalized_base_url))
''')
    for name, source in sources.items():
        ast.parse(source, filename=name)
    return {root / name: source for name, source in sources.items()}


def main():
    root = Path(sys.argv[1]).resolve()
    for path, source in patched_sources(root).items():
        path.write_text(source)
    print("Applied Alpha bridge to pinned Hermes source")


if __name__ == "__main__":
    main()
