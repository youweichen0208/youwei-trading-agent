from __future__ import annotations
import uuid
from dataclasses import dataclass


EXPLORATORY_CODE_VERSION = "exploratory-research-v1"


EXPLORATORY_JOB_KIND = "research.exploratory"


EXPLORATORY_MAX_ATTEMPTS = 3


REPORT_FORMAT = "exploratory-report-v1"


VALID_HORIZONS = (1, 20, 60)


EVIDENCE_LOOKBACK_CALENDAR_DAYS = 90


class ExploratoryResearchError(Exception):
    """The research submission cannot proceed (bad request or missing
    registered context); never a research execution failure."""


class ExploratoryNotFound(Exception):
    """The research id does not exist under this tenant."""


class IdempotencyConflictError(Exception):
    """Same idempotency key with a different request."""


@dataclass
class ExploratorySubmitResult:
    research_id: uuid.UUID
    run_id: uuid.UUID
    job_id: uuid.UUID
    created: bool


class ExploratoryExecutionError(Exception):
    """Infrastructure-level failure of the execution loop itself."""


@dataclass
class ReportSave:
    report_version: int
    created: bool
    fenced: bool
