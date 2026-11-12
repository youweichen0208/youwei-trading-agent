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
