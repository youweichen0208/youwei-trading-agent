"""Locked-image context lookup checks; synthetic profiles, no external network/model."""

import contextlib
import io
import json
import os
import socket
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace


def main():
    if os.environ.get("YTA_CONTEXT_SCOPE_PROBE") != "1":
        raise RuntimeError("Explicit isolated probe required")
    active = {
        name
        for _, name in socket.if_nameindex()
        if int(Path("/sys/class/net", name, "flags").read_text(), 16) & 1
    }
    assert active == {"lo"}
    root = Path("/tmp/yta-context-scope")
    root.mkdir(mode=0o700)
    os.environ["HERMES_HOME"] = str(root)
    os.chdir(root)
    profiles = [root / "profiles" / name for name in ("left", "right")]
    markers = ["YTA_CONTEXT_LEFT_123456", "YTA_CONTEXT_RIGHT_654321"]
    for home, marker in zip(profiles, markers, strict=True):
        skill = home / "skills" / ("scope-" + marker.lower())
        skill.mkdir(parents=True)
        (home / "SOUL.md").write_text("Identity fixture: " + marker + "\n")
        (home / "config.yaml").write_text(
            json.dumps(
                {
                    "skills": {
                        "external_dirs": [],
                        "auto_load": ["scope-" + marker.lower()],
                    },
                    "memory": {"memory_enabled": False, "user_profile_enabled": False},
                }
            )
        )
        (skill / "SKILL.md").write_text(
            "---\nname: scope-"
            + marker.lower()
            + "\ndescription: "
            + marker
            + " synthetic fixture.\n---\nDo nothing. "
            + marker
            + "\n"
        )
    with contextlib.redirect_stdout(io.StringIO()):
        from agent.prompt_builder import build_skills_system_prompt, load_soul_md
        from agent.system_prompt import _auto_load_parts, _skills_prompt
        from gateway.run import _profile_runtime_scope
        from hermes_constants import get_hermes_home
        from tools.memory_tool import get_memory_dir

        def check(index):
            current = index % 2
            home, own, other = profiles[current], markers[current], markers[1 - current]
            with _profile_runtime_scope(home):
                assert get_hermes_home() == home
                assert get_memory_dir() == home / "memories"
                soul = load_soul_md() or ""
                skills = build_skills_system_prompt()
                assert own in soul and other not in soul
                assert own in skills and other not in skills
                with _profile_runtime_scope(profiles[1 - current]):
                    assert other in (load_soul_md() or "")
                assert get_hermes_home() == home
            return True

        # Repeated interleaving exercises profile-sensitive shared prompt caches.
        with ThreadPoolExecutor(max_workers=8) as pool:
            assert all(pool.map(check, range(64)))

        # Explicit home pinning must also work in a worker without the caller's ContextVar.
        def pinned(index):
            home, own, other = profiles[index], markers[index], markers[1 - index]
            assert get_hermes_home() == root
            soul = load_soul_md(home_override=home) or ""
            skills = build_skills_system_prompt(skills_dir_override=home / "skills")
            assert own in soul and other not in soul
            assert own in skills and other not in skills
            return True

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert all(pool.map(pinned, (0, 1)))
        # Scope selection is not an OS sandbox: the same UID can directly read both homes.
        assert markers[1] in (profiles[1] / "SOUL.md").read_text()
        # Exercise the actual prompt assembly gates with present, configured skills.
        # A positive control ensures an empty result is not a missing-fixture success.
        for home, marker in zip(profiles, markers, strict=True):
            with _profile_runtime_scope(home):
                finance = SimpleNamespace(
                    valid_tool_names={
                        "mcp__yta_finance__market_snapshot",
                        "mcp__yta_finance__indicators",
                    },
                    platform="api_server",
                )
                assert _skills_prompt(finance) == ""
                assert _auto_load_parts(finance) == []
                skill_agent = SimpleNamespace(
                    valid_tool_names={"skills_list", "skill_view"}, platform="api_server"
                )
                assert marker in _skills_prompt(skill_agent)
                assert marker in "".join(_auto_load_parts(skill_agent))
    print(
        json.dumps(
            {
                "concurrent_profile_reads": 64,
                "workers": 8,
                "nested_scope_restored": True,
                "soul_isolated": True,
                "skills_index_isolated": True,
                "memory_directory_isolated": True,
                "shared_uid_can_read_other_profile_file": True,
                "explicit_home_worker_checks": 2,
                "external_model_requests": 0,
                "finance_only_skills_index_empty": True,
                "finance_only_auto_load_empty": True,
                "skill_tools_positive_controls": 2,
                "network": "none",
                "qualification_status": "UNVERIFIED",
                "scope": "Locked lookup/cache functions; not a Gateway Run or OS file sandbox",
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(
            json.dumps({"error": "CONTEXT_SCOPE_PROBE_FAILED", "type": type(error).__name__})
        ) from None
