"""Hermes research runtime (S07).

Independent Python 3.14 environment. This package adapts Hermes for the
platform: it turns a Controller-frozen plan/evidence bundle into a
ResearchProposal, enforcing the research-tool whitelist, memory/session
isolation, and run-scoped context. It holds no Ledger, database, or
supplier credentials; the Controller validates and seals the returned
proposal. Hermes upstream is pinned by full commit SHA (see UPSTREAMS.md
and infra/upstreams.lock.yaml).
"""

__version__ = "0.1.0"
