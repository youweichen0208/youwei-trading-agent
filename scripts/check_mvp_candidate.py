"""Inspect current local candidate artifacts; never declares release qualification."""

import hashlib
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True, timeout=60).strip()


def main():
    tags = {
        "bff": "yta-bff:mvp",
        "web": "yta-web:mvp",
        "alpha": "yta-alpha:mvp",
        "finance_tools": "yta-finance-tools:mvp",
        "migrator": "yta-migrator:local",
    }
    images = {
        name: docker("image", "inspect", tag, "--format", "{{.Id}}") for name, tag in tags.items()
    }
    checks = {}
    for name, command in {
        "bff": [
            "node",
            "--input-type=module",
            "-e",
            'import {HermesResearch} from "./dist/hermes-research.js"; '
            'import {HermesResearchDriver} from "./dist/hermes-driver.js"; '
            'import {createBff} from "./dist/app.js"; '
            'import {configuredDriver} from "./dist/configured-driver.js"; '
            "if (![HermesResearch,HermesResearchDriver,createBff,configuredDriver] "
            '.every(x=>typeof x==="function")) process.exit(1); '
            'console.log("packaged-coordinator-present")',
        ],
        "alpha": [
            "python",
            "-c",
            "from yta_alpha.api import create_app; "
            "from yta_alpha.research_tools import ResearchTools; "
            "assert callable(create_app) and callable(ResearchTools.observe); "
            'print("packaged-alpha-present")',
        ],
        "finance_tools": [
            "python",
            "-c",
            "from yta_tools.server import Settings,create_server; "
            'assert "scoped_receipts" in Settings.model_fields; '
            'print("packaged-scoped-tools-present")',
        ],
    }.items():
        checks[name] = docker(
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--entrypoint",
            command[0],
            images[name],
            *command[1:],
        )
    catalog = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((ROOT / "infra/migrations").glob("*.sql"))
    }
    copied = json.loads(
        docker(
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--entrypoint",
            "python",
            images["migrator"],
            "-c",
            "import pathlib,hashlib,json; "
            "print(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() "
            'for p in pathlib.Path("/app/infra/migrations").glob("*.sql")}))',
        )
    )
    assert copied == catalog
    excluded = {
        "node_modules",
        ".next",
        ".next-e2e",
        "dist",
        "__pycache__",
        ".pytest_cache",
        "tests",
        "scripts",
    }
    sources = {}
    for folder in ["apps", "packages", "services", "infra/docker", "infra/migrations"]:
        for root, dirs, files in os.walk(ROOT / folder):
            dirs[:] = [d for d in dirs if d not in excluded]
            for name in sorted(files):
                if name.startswith(".env") or name.endswith(".tsbuildinfo"):
                    continue
                path = Path(root) / name
                sources[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in [
        "package.json",
        "pnpm-lock.yaml",
        "pnpm-workspace.yaml",
        "tsconfig.base.json",
        "pyproject.toml",
        "uv.lock",
        ".dockerignore",
    ]:
        sources[name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
    result = {
        "status": "LOCAL_CANDIDATE_NOT_RELEASE_QUALIFIED",
        "images": images,
        "package_checks": checks,
        "migration_catalog": catalog,
        "source_sha256": sources,
        "source_kind": "working directory without Git metadata",
        "limitations": [
            "No remote CI/review",
            "No staging deployment/rollback",
            "No LIVE market qualification",
            "Hermes locked capability gates UNVERIFIED",
            "Hermes requires explicit local configuration; "
            "staging/production capability gates remain closed",
        ],
    }
    path = ROOT / "infra/verification/mvp-candidate-observation.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": result["status"],
                "images": images,
                "package_checks": checks,
                "migrations": len(catalog),
                "source_files": len(sources),
            }
        )
    )


if __name__ == "__main__":
    main()
