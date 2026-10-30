import logging
import secrets
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from opentelemetry import trace
from starlette.concurrency import run_in_threadpool

from yta_alpha.backtest_catalog import BacktestPage, backtest_page
from yta_alpha.backtest_contracts import BacktestId, BacktestResponse
from yta_alpha.backtest_registry import BacktestInput, BacktestRegistry
from yta_alpha.backtest_reports import backtest_response
from yta_alpha.calendar import TradingCalendar
from yta_alpha.calibrated_predictions import CalibratedExperimentalPrediction, calibrated_predict
from yta_alpha.calibration_contracts import CalibrationReportResponse
from yta_alpha.calibration_registry import CalibrationRegistry
from yta_alpha.calibration_reports import calibration_report
from yta_alpha.errors import DomainError
from yta_alpha.evaluation import EvaluationRequest, create_report, get_report, list_reports
from yta_alpha.experimental_predictions import (
    ExperimentalPrediction,
    ExperimentalRequest,
    experimental_predict,
)
from yta_alpha.fixtures import replay_observation, replay_snapshot
from yta_alpha.forecast import forecast, snapshot_hash
from yta_alpha.indicators import indicators
from yta_alpha.market_cache import MarketCache, MarketFreshness
from yta_alpha.model_catalog import ModelPage, model_page
from yta_alpha.model_evaluation_catalog import EvaluationPage, report_page
from yta_alpha.model_evaluation_contracts import (
    CalibrationId,
    EvaluationComparisonRequest,
    ExperimentalPriceReport,
    ExperimentalReportComparison,
    ModelId,
)
from yta_alpha.model_evaluation_registry import ModelEvaluationRegistry
from yta_alpha.model_proposal_catalog import (
    ProposalPage,
    proposal_page,
    review_proposal,
    review_proposal_page,
)
from yta_alpha.model_proposals import ModelProposalRegistry, ProposalRequest, ProposalResponse
from yta_alpha.model_quality_catalog import QualityPolicyPage, policy_page
from yta_alpha.model_quality_contracts import (
    AssessmentId,
    PolicyId,
    QualityAssessmentRequest,
    QualityAssessmentResponse,
    QualityPolicyResponse,
)
from yta_alpha.model_quality_gate import ModelQualityGateRequest
from yta_alpha.model_quality_gate_contracts import ModelQualityGateResponse, QualityGateId
from yta_alpha.model_quality_gate_registry import ModelQualityGateRegistry
from yta_alpha.model_quality_registry import ModelQualityRegistry
from yta_alpha.model_registry import ModelRegistry
from yta_alpha.model_review_contracts import ModelReviewPage, ModelReviewResponse
from yta_alpha.model_review_registry import (
    CombinedReviewSubmission,
    ModelReviewRegistry,
    ReviewSubmission,
)
from yta_alpha.models import (
    DataMode,
    DraftRequest,
    Forecast,
    ForecastRequest,
    FreezeRequest,
    MarketSnapshot,
    ToolMarketSnapshot,
)
from yta_alpha.probability_quality_catalog import ProbabilityPolicyPage
from yta_alpha.probability_quality_catalog import policy_page as probability_policy_page
from yta_alpha.probability_quality_contracts import (
    ProbabilityAssessmentId,
    ProbabilityAssessmentRequest,
    ProbabilityAssessmentResponse,
    ProbabilityPolicyId,
    ProbabilityPolicyResponse,
)
from yta_alpha.probability_quality_registry import ProbabilityQualityRegistry
from yta_alpha.qualified_market import QualifiedMarket
from yta_alpha.readiness import readiness
from yta_alpha.research_tools import ResearchTools, ToolContextRequest, ToolObservationRequest
from yta_alpha.settings import Settings
from yta_alpha.store import ResearchStore
from yta_alpha.symbols import SymbolHistory, symbol_history
from yta_alpha.tracing import AlphaTracing
from yta_alpha.training_evidence_catalog import TrainingEvidencePage, training_evidence_page
from yta_alpha.training_evidence_contracts import TrainingEvidenceId, TrainingEvidenceResponse
from yta_alpha.training_evidence_registry import TrainingEvidenceRegistry
from yta_alpha.training_evidence_reports import training_evidence_response

log = logging.getLogger("yta.alpha")


def create_app(
    settings: Settings | None = None,
    clock: Callable[[], datetime] | None = None,
    calendar: TradingCalendar | None = None,
) -> FastAPI:
    settings = settings or Settings()
    clock = clock or (lambda: datetime.now(UTC))
    calendar = calendar or TradingCalendar()
    tracing = AlphaTracing(settings)

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            await run_in_threadpool(tracing.shutdown)

    app = FastAPI(
        title="YTA Alpha Engine", version="0.1.0", docs_url=None, redoc_url=None, lifespan=lifespan
    )

    def store() -> ResearchStore:
        if settings.database_url is None:
            raise DomainError("STORAGE_UNAVAILABLE", "Database is not configured", status=503)
        return ResearchStore(settings.database_url.get_secret_value(), calendar, settings.git_sha)

    qualified_market = (
        QualifiedMarket(settings.database_url.get_secret_value(), calendar, settings.data_mode)
        if settings.market_provider == "MASSIVE" and settings.database_url
        else None
    )

    def load_market_snapshot() -> MarketSnapshot:
        if settings.data_mode == DataMode.REPLAY and settings.env in ("development", "test"):
            return replay_snapshot(calendar, count=500)
        if qualified_market is not None:
            return qualified_market.latest(clock())
        raise DomainError(
            "DATA_UNAVAILABLE", "No verified real market provider configured", status=503
        )

    market_cache = MarketCache(
        load_market_snapshot,
        settings.data_mode,
        "synthetic-fixture"
        if settings.data_mode == DataMode.REPLAY
        else "massive"
        if qualified_market is not None
        else "unconfigured",
        calendar,
        clock,
    )

    def snapshot_for_research() -> MarketSnapshot:
        return market_cache.read(require_fresh=True)

    def owner_id(x_yta_owner_id: Annotated[UUID, Header()]) -> UUID:
        # Accepted only after service authentication. Public BFF derives this header from login.
        return x_yta_owner_id

    def fact_clock(saved: dict) -> datetime:
        result = Forecast.model_validate(saved["forecast"])
        if result.data_mode != settings.data_mode:
            raise DomainError("DATA_MODE_MISMATCH", "Stored record belongs to another environment")
        if settings.data_mode == DataMode.REPLAY and settings.env in ("development", "test"):
            return result.as_of
        return clock()

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        # Do not echo arbitrary attacker-supplied trace IDs into logs/headers.
        request.state.request_id = f"req_{uuid4().hex}"
        with tracing.tracer.start_as_current_span(
            "alpha.http",
            context=tracing.parent(request.headers),
            kind=trace.SpanKind.SERVER,
            attributes={"yta.request_id": request.state.request_id},
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                response = await call_next(request)
                if response.status_code >= 500:
                    span.set_status(trace.StatusCode.ERROR)
                span.set_attribute("http.response.status_code", response.status_code)
            except Exception:
                span.set_status(trace.StatusCode.ERROR)
                raise
            finally:
                route = request.scope.get("route")
                path = getattr(route, "path", "UNMATCHED")
                method = (
                    request.method
                    if request.method
                    in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
                    else "OTHER"
                )
                span.update_name(f"{method} {path}")
                span.set_attributes({"http.route": path, "http.request.method": method})
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    def error_response(request: Request, code: str, message: str, status: int, retryable=False):
        return JSONResponse(
            status_code=status,
            content={
                "error": {
                    "code": code,
                    "message": message,
                    "request_id": request.state.request_id,
                    "retryable": retryable,
                }
            },
        )

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, error: DomainError):
        return error_response(request, error.code, error.message, error.status, error.retryable)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request: Request, error: RequestValidationError):
        # Validation details can echo tokens or entire supplied documents. Return only locations.
        fields = [".".join(map(str, item["loc"])) for item in error.errors()]
        return error_response(
            request, "INVALID_REQUEST", "Invalid fields: " + ", ".join(fields), 422
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception):
        log.error(
            "alpha failure request_id=%s kind=%s", request.state.request_id, type(error).__name__
        )
        return error_response(request, "INTERNAL_ERROR", "Request failed", 500, True)

    def authenticate(authorization: Annotated[str | None, Header()] = None):
        expected = f"Bearer {settings.alpha_token.get_secret_value()}"
        if not authorization or not secrets.compare_digest(authorization, expected):
            raise DomainError("UNAUTHORIZED", "Service authentication required", status=401)

    auth = [Depends(authenticate)]

    def authenticate_tools(authorization: Annotated[str | None, Header()] = None):
        token = settings.alpha_tools_token
        if (
            token is None
            or not authorization
            or not secrets.compare_digest(authorization, f"Bearer {token.get_secret_value()}")
        ):
            raise DomainError("UNAUTHORIZED", "Tool authentication required", status=401)

    tool_auth = [Depends(authenticate_tools)]

    @app.get(
        "/internal/v1/tools/market-snapshot",
        dependencies=tool_auth,
        response_model=ToolMarketSnapshot,
    )
    def tool_market_snapshot():
        snapshot = snapshot_for_research()
        return ToolMarketSnapshot(snapshot=snapshot, data_snapshot_hash=snapshot_hash(snapshot))

    @app.get("/internal/v1/tools/indicators", dependencies=tool_auth)
    def tool_indicators():
        return indicators(snapshot_for_research(), calendar)

    @app.post("/internal/v1/research-tool-contexts", dependencies=auth)
    def create_research_tool_context(
        body: ToolContextRequest, owner: Annotated[UUID, Depends(owner_id)]
    ):
        return ResearchTools(store()).create(
            owner, body.run_id, settings.data_mode, snapshot_for_research
        )

    @app.get("/internal/v1/research-tool-contexts/{run_id}", dependencies=auth)
    def get_research_tool_context(run_id: UUID, owner: Annotated[UUID, Depends(owner_id)]):
        return ResearchTools(store()).get(owner, run_id, settings.data_mode)

    @app.post("/internal/v1/research-tool-contexts/{run_id}/draft", dependencies=auth)
    def research_tool_draft(run_id: UUID, owner: Annotated[UUID, Depends(owner_id)]):
        return ResearchTools(store()).draft(owner, run_id, settings.data_mode, clock)

    @app.post("/internal/v1/tools/observations", dependencies=tool_auth)
    def observe_research_tool(body: ToolObservationRequest):
        return ResearchTools(store()).observe(body, settings.data_mode)

    def check_mode(snapshot: MarketSnapshot):
        if snapshot.data_mode != settings.data_mode:
            raise DomainError(
                "DATA_MODE_MISMATCH", "Snapshot does not match configured environment"
            )

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "alpha-engine", "data_mode": settings.data_mode.value}

    @app.get("/internal/v1/ready", dependencies=auth)
    def ready():
        result = readiness(settings)
        return JSONResponse(result, status_code=200 if result["status"] == "ready" else 503)

    @app.get("/internal/v1/health", dependencies=auth)
    def internal_health():
        return {
            **health(),
            "calendar_version": calendar.version,
            "git_sha": settings.git_sha,
            "capabilities": ["baseline.predict", "indicators.compute"],
        }

    @app.post("/internal/v1/predict", dependencies=auth, response_model=Forecast)
    def predict(body: ForecastRequest):
        check_mode(body.snapshot)
        return forecast(body.snapshot, now=clock(), calendar=calendar, git_sha=settings.git_sha)

    @app.get(
        "/internal/v1/experimental-calibrations/{calibration_id}",
        dependencies=auth,
        response_model=CalibrationReportResponse,
    )
    def experimental_calibration_report(calibration_id: CalibrationId):
        if settings.experimental_models != "ENABLED":
            raise DomainError(
                "EXPERIMENTAL_MODELS_DISABLED", "Calibration reports disabled", status=503
            )
        if settings.database_url is None:
            raise DomainError("STORAGE_UNAVAILABLE", "Database is not configured", status=503)
        registry = CalibrationRegistry(settings.database_url.get_secret_value(), calendar)
        return calibration_report(registry, calibration_id, settings.data_mode)

    def review_registry() -> ModelReviewRegistry:
        if settings.experimental_models != "ENABLED":
            raise DomainError(
                "EXPERIMENTAL_MODELS_DISABLED", "Model review is disabled", status=503
            )
        if settings.database_url is None:
            raise DomainError("STORAGE_UNAVAILABLE", "Database is not configured", status=503)
        return ModelReviewRegistry(settings.database_url.get_secret_value(), calendar)

    @app.post("/internal/v1/model-reviews", dependencies=auth, response_model=ModelReviewResponse)
    def submit_model_review(
        body: ReviewSubmission | CombinedReviewSubmission,
        idempotency_key: Annotated[UUID, Header()],
        reviewer: Annotated[UUID, Depends(owner_id)],
    ):
        # Only the trusted BFF may authenticate/authorize this identity for review.
        if isinstance(body, CombinedReviewSubmission):
            return review_registry().create_combined(
                reviewer, idempotency_key, settings.data_mode, body
            )
        return review_registry().create(reviewer, idempotency_key, settings.data_mode, body)

    @app.get(
        "/internal/v1/model-reviews/{review_id}",
        dependencies=auth,
        response_model=ModelReviewResponse,
    )
    def model_review(review_id: UUID):
        return review_registry().get(review_id, settings.data_mode)

    @app.get(
        "/internal/v1/model-proposals/{proposal_id}/reviews",
        dependencies=auth,
        response_model=ModelReviewPage,
    )
    def model_review_history(
        proposal_id: UUID,
        cursor: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
    ):
        return review_registry().history(
            proposal_id, settings.data_mode, cursor=cursor, limit=limit
        )

    def quality_registry() -> ModelQualityRegistry:
        if settings.experimental_models != "ENABLED":
            raise DomainError(
                "EXPERIMENTAL_MODELS_DISABLED", "Historical quality is disabled", status=503
            )
        if settings.database_url is None:
            raise DomainError("STORAGE_UNAVAILABLE", "Database is not configured", status=503)
        return ModelQualityRegistry(settings.database_url.get_secret_value(), calendar)

    @app.get(
        "/internal/v1/historical-quality-policies",
        dependencies=auth,
        response_model=QualityPolicyPage,
    )
    def historical_quality_policies(
        horizon_sessions: Literal["1", "3", "5", "10", "20"] | None = None,
        cursor: PolicyId | None = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
    ):
        return policy_page(
            quality_registry(),
            settings.data_mode,
            horizon_sessions=int(horizon_sessions) if horizon_sessions is not None else None,
            cursor=cursor,
            limit=limit,
        )

    @app.get(
        "/internal/v1/historical-quality-policies/{policy_id}",
        dependencies=auth,
        response_model=QualityPolicyResponse,
    )
    def historical_quality_policy(policy_id: PolicyId):
        from yta_alpha.forecast import content_hash

        policy = quality_registry().get_policy(policy_id, data_mode=settings.data_mode)
        return {
            "id": policy_id,
            "content_hash": content_hash(policy.model_dump(mode="json")),
            "policy": policy,
        }

    @app.post(
        "/internal/v1/historical-quality-assessments",
        dependencies=auth,
        response_model=QualityAssessmentResponse,
    )
    def create_quality_assessment(body: QualityAssessmentRequest):
        registry = quality_registry()
        registry.get_policy(body.policy_id, data_mode=settings.data_mode)
        identity = registry.create(
            body.policy_id, body.candidate_evaluation_id, reference_id=body.reference_evaluation_id
        )
        return registry.get(identity, data_mode=settings.data_mode)

    @app.get(
        "/internal/v1/historical-quality-assessments/{assessment_id}",
        dependencies=auth,
        response_model=QualityAssessmentResponse,
    )
    def historical_quality_assessment(assessment_id: AssessmentId):
        return quality_registry().get(assessment_id, data_mode=settings.data_mode)

    @app.get(
        "/internal/v1/training-evidence/{evidence_id}",
        dependencies=auth,
        response_model=TrainingEvidenceResponse,
    )
    def get_training_evidence(evidence_id: TrainingEvidenceId):
        return training_evidence_response(training_registry(), evidence_id, mode=settings.data_mode)

    def training_registry() -> TrainingEvidenceRegistry:
        if settings.experimental_models != "ENABLED":
            raise DomainError(
                "EXPERIMENTAL_MODELS_DISABLED", "Training evidence disabled", status=503
            )
        if settings.database_url is None:
            raise DomainError("STORAGE_UNAVAILABLE", "Database is not configured", status=503)
        return TrainingEvidenceRegistry(settings.database_url.get_secret_value(), calendar)

    @app.get(
        "/internal/v1/experimental-models/{model_id}/training-evidence",
        dependencies=auth,
        response_model=TrainingEvidencePage,
    )
    def list_training_evidence(
        model_id: ModelId,
        cursor: Annotated[str | None, Query(max_length=100)] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
    ):
        return training_evidence_page(
            training_registry(), model_id, settings.data_mode, cursor=cursor, limit=limit
        )

    def backtest_registry() -> BacktestRegistry:
        if settings.experimental_models != "ENABLED":
            raise DomainError(
                "EXPERIMENTAL_MODELS_DISABLED", "Backtest reports disabled", status=503
            )
        if settings.database_url is None:
            raise DomainError("STORAGE_UNAVAILABLE", "Database is not configured", status=503)
        return BacktestRegistry(settings.database_url.get_secret_value(), calendar)

    @app.get("/internal/v1/backtest-reports", dependencies=auth, response_model=BacktestPage)
    def list_backtest_reports(
        cursor: Annotated[str | None, Query(max_length=100)] = None,
        model_id: Annotated[str | None, Query(max_length=100)] = None,
        status: Literal["COMPLETE", "INCOMPLETE"] | None = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
    ):
        return backtest_page(
            backtest_registry(),
            settings.data_mode,
            cursor=cursor,
            model_id=model_id,
            status=status,
            limit=limit,
        )

    @app.post("/internal/v1/backtest-reports", dependencies=auth, response_model=BacktestResponse)
    def create_backtest_report(body: BacktestInput):
        registry = backtest_registry()
        identity = registry.create(settings.data_mode, body)
        return backtest_response(registry, identity, settings.data_mode)

    @app.get(
        "/internal/v1/backtest-reports/{report_id}",
        dependencies=auth,
        response_model=BacktestResponse,
    )
    def get_backtest_report(report_id: BacktestId):
        return backtest_response(backtest_registry(), report_id, settings.data_mode)

    def quality_gate_registry() -> ModelQualityGateRegistry:
        if settings.experimental_models != "ENABLED":
            raise DomainError(
                "EXPERIMENTAL_MODELS_DISABLED", "Combined model quality is disabled", status=503
            )
        if settings.database_url is None:
            raise DomainError("STORAGE_UNAVAILABLE", "Database is not configured", status=503)
        return ModelQualityGateRegistry(settings.database_url.get_secret_value(), calendar)

    @app.post(
        "/internal/v1/model-quality-gates",
        dependencies=auth,
        response_model=ModelQualityGateResponse,
    )
    def create_model_quality_gate(
        body: ModelQualityGateRequest,
        owner: Annotated[UUID, Depends(owner_id)],
    ):
        registry = quality_gate_registry()
        identity = registry.create(owner, settings.data_mode, body)
        return registry.get(owner, settings.data_mode, identity)

    @app.get(
        "/internal/v1/model-quality-gates/{gate_id}",
        dependencies=auth,
        response_model=ModelQualityGateResponse,
    )
    def model_quality_gate(gate_id: QualityGateId, owner: Annotated[UUID, Depends(owner_id)]):
        return quality_gate_registry().get(owner, settings.data_mode, gate_id)

    @app.post(
        "/internal/v1/model-review-quality-gates",
        dependencies=auth,
        response_model=ModelQualityGateResponse,
    )
    def create_review_quality_gate(
        body: ModelQualityGateRequest,
        reviewer: Annotated[UUID, Depends(owner_id)],
    ):
        registry = quality_gate_registry()
        identity = registry.create_for_review(reviewer, settings.data_mode, body)
        return registry.get_for_review(settings.data_mode, identity)

    @app.get(
        "/internal/v1/model-review-quality-gates/{gate_id}",
        dependencies=auth,
        response_model=ModelQualityGateResponse,
    )
    def review_quality_gate(gate_id: QualityGateId, reviewer: Annotated[UUID, Depends(owner_id)]):
        return quality_gate_registry().get_for_review(settings.data_mode, gate_id)

    def probability_quality_registry() -> ProbabilityQualityRegistry:
        if settings.experimental_models != "ENABLED":
            raise DomainError(
                "EXPERIMENTAL_MODELS_DISABLED", "Probability quality is disabled", status=503
            )
        if settings.database_url is None:
            raise DomainError("STORAGE_UNAVAILABLE", "Database is not configured", status=503)
        return ProbabilityQualityRegistry(settings.database_url.get_secret_value(), calendar)

    @app.get(
        "/internal/v1/probability-quality-policies",
        dependencies=auth,
        response_model=ProbabilityPolicyPage,
    )
    def probability_quality_policies(
        horizon_sessions: Literal["1", "3", "5", "10", "20"] | None = None,
        cursor: ProbabilityPolicyId | None = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
    ):
        return probability_policy_page(
            probability_quality_registry(),
            settings.data_mode,
            horizon_sessions=int(horizon_sessions) if horizon_sessions is not None else None,
            cursor=cursor,
            limit=limit,
        )

    @app.get(
        "/internal/v1/probability-quality-policies/{policy_id}",
        dependencies=auth,
        response_model=ProbabilityPolicyResponse,
    )
    def probability_quality_policy(policy_id: ProbabilityPolicyId):
        from yta_alpha.forecast import content_hash

        policy = probability_quality_registry().get_policy(policy_id, data_mode=settings.data_mode)
        return {
            "id": policy_id,
            "content_hash": content_hash(policy.model_dump(mode="json")),
            "policy": policy,
        }

    @app.post(
        "/internal/v1/probability-quality-assessments",
        dependencies=auth,
        response_model=ProbabilityAssessmentResponse,
    )
    def create_probability_quality_assessment(body: ProbabilityAssessmentRequest):
        registry = probability_quality_registry()
        registry.get_policy(body.policy_id, data_mode=settings.data_mode)
        identity = registry.create(body.policy_id, body.calibration_id)
        return registry.get(identity, data_mode=settings.data_mode)

    @app.get(
        "/internal/v1/probability-quality-assessments/{assessment_id}",
        dependencies=auth,
        response_model=ProbabilityAssessmentResponse,
    )
    def probability_quality_assessment(assessment_id: ProbabilityAssessmentId):
        return probability_quality_registry().get(assessment_id, data_mode=settings.data_mode)

    def proposals() -> ModelProposalRegistry:
        if settings.experimental_models != "ENABLED":
            raise DomainError(
                "EXPERIMENTAL_MODELS_DISABLED", "Experimental proposals are disabled", status=503
            )
        if settings.database_url is None:
            raise DomainError("STORAGE_UNAVAILABLE", "Database is not configured", status=503)
        return ModelProposalRegistry(settings.database_url.get_secret_value(), calendar)

    @app.get("/internal/v1/model-review-proposals", dependencies=auth, response_model=ProposalPage)
    def review_proposals(
        cursor: Annotated[UUID | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
    ):
        return review_proposal_page(proposals(), settings.data_mode, cursor=cursor, limit=limit)

    @app.get(
        "/internal/v1/model-review-proposals/{proposal_id}",
        dependencies=auth,
        response_model=ProposalResponse,
    )
    def review_proposal_detail(proposal_id: UUID):
        return review_proposal(proposals(), settings.data_mode, proposal_id)

    @app.get("/internal/v1/model-proposals", dependencies=auth, response_model=ProposalPage)
    def model_proposals(
        owner: Annotated[UUID, Depends(owner_id)],
        cursor: Annotated[UUID | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
    ):
        return proposal_page(proposals(), owner, settings.data_mode, cursor=cursor, limit=limit)

    @app.post("/internal/v1/model-proposals", dependencies=auth, response_model=ProposalResponse)
    def submit_model_proposal(
        body: ProposalRequest,
        idempotency_key: Annotated[UUID, Header()],
        owner: Annotated[UUID, Depends(owner_id)],
    ):
        return proposals().create(owner, idempotency_key, settings.data_mode, body)

    @app.get(
        "/internal/v1/model-proposals/{proposal_id}",
        dependencies=auth,
        response_model=ProposalResponse,
    )
    def model_proposal(proposal_id: UUID, owner: Annotated[UUID, Depends(owner_id)]):
        return proposals().get(owner, settings.data_mode, proposal_id)

    @app.get(
        "/internal/v1/experimental-models/{model_id}/evaluations",
        dependencies=auth,
        response_model=EvaluationPage,
    )
    def experimental_evaluations(
        model_id: str,
        cursor: Annotated[str | None, Query(max_length=100)] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
    ):
        if settings.experimental_models != "ENABLED":
            raise DomainError(
                "EXPERIMENTAL_MODELS_DISABLED", "Experimental evaluation is disabled", status=503
            )
        if settings.database_url is None:
            raise DomainError("STORAGE_UNAVAILABLE", "Database is not configured", status=503)
        return report_page(
            ModelEvaluationRegistry(settings.database_url.get_secret_value(), calendar),
            model_id,
            settings.data_mode,
            cursor=cursor,
            limit=limit,
        )

    @app.post(
        "/internal/v1/experimental-evaluations/compare",
        dependencies=auth,
        response_model=ExperimentalReportComparison,
    )
    def experimental_evaluation_comparison(body: EvaluationComparisonRequest):
        if settings.experimental_models != "ENABLED":
            raise DomainError(
                "EXPERIMENTAL_MODELS_DISABLED", "Experimental evaluation is disabled", status=503
            )
        if settings.database_url is None:
            raise DomainError("STORAGE_UNAVAILABLE", "Database is not configured", status=503)
        registry = ModelEvaluationRegistry(settings.database_url.get_secret_value(), calendar)
        return registry.compare(body.evaluation_ids, data_mode=settings.data_mode)

    @app.get(
        "/internal/v1/experimental-evaluations/{evaluation_id}",
        dependencies=auth,
        response_model=ExperimentalPriceReport,
    )
    def experimental_evaluation(evaluation_id: str):
        if settings.experimental_models != "ENABLED":
            raise DomainError(
                "EXPERIMENTAL_MODELS_DISABLED", "Experimental evaluation is disabled", status=503
            )
        if settings.database_url is None:
            raise DomainError("STORAGE_UNAVAILABLE", "Database is not configured", status=503)
        registry = ModelEvaluationRegistry(settings.database_url.get_secret_value(), calendar)
        return registry.get(evaluation_id, data_mode=settings.data_mode)

    @app.get("/internal/v1/experimental-models", dependencies=auth, response_model=ModelPage)
    def experimental_models(
        cursor: Annotated[str | None, Query(max_length=100)] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        horizon_sessions: Literal["1", "3", "5", "10", "20"] | None = None,
    ):
        if settings.experimental_models != "ENABLED":
            raise DomainError(
                "EXPERIMENTAL_MODELS_DISABLED", "Experimental inference is disabled", status=503
            )
        if settings.database_url is None:
            raise DomainError("STORAGE_UNAVAILABLE", "Database is not configured", status=503)
        return model_page(
            ModelRegistry(settings.database_url.get_secret_value()),
            settings.data_mode,
            horizon=int(horizon_sessions) if horizon_sessions is not None else None,
            cursor=cursor,
            limit=limit,
        )

    @app.post(
        "/internal/v1/experimental-models/{model_id}/predict",
        dependencies=auth,
        response_model=ExperimentalPrediction,
    )
    def predict_experimental_model(model_id: str, body: ExperimentalRequest):
        if settings.experimental_models != "ENABLED":
            raise DomainError(
                "EXPERIMENTAL_MODELS_DISABLED", "Experimental inference is disabled", status=503
            )
        check_mode(body.snapshot)
        if settings.database_url is None:
            raise DomainError("STORAGE_UNAVAILABLE", "Database is not configured", status=503)
        return experimental_predict(
            ModelRegistry(settings.database_url.get_secret_value()),
            model_id,
            body,
            calendar=calendar,
            now=clock(),
            replay=settings.data_mode == DataMode.REPLAY
            and settings.env in ("development", "test"),
        )

    @app.post(
        "/internal/v1/experimental-models/{model_id}/calibrations/{calibration_id}/predict",
        dependencies=auth,
        response_model=CalibratedExperimentalPrediction,
    )
    def predict_calibrated_model(model_id: str, calibration_id: str, body: ExperimentalRequest):
        if settings.experimental_models != "ENABLED":
            raise DomainError(
                "EXPERIMENTAL_MODELS_DISABLED", "Experimental inference is disabled", status=503
            )
        check_mode(body.snapshot)
        if settings.database_url is None:
            raise DomainError("STORAGE_UNAVAILABLE", "Database is not configured", status=503)
        return calibrated_predict(
            CalibrationRegistry(settings.database_url.get_secret_value(), calendar),
            model_id,
            calibration_id,
            body,
            now=clock(),
            replay=settings.data_mode == DataMode.REPLAY
            and settings.env in ("development", "test"),
        )

    @app.post("/internal/v1/indicators", dependencies=auth)
    def compute_indicators(body: MarketSnapshot):
        check_mode(body)
        return indicators(body, calendar)

    @app.get("/internal/v1/symbols/{symbol}", dependencies=auth, response_model=SymbolHistory)
    def symbols(symbol: str):
        if settings.database_url is None:
            raise DomainError("DATA_UNAVAILABLE", "Symbol reference is unavailable", status=503)
        return symbol_history(settings.database_url.get_secret_value(), symbol)

    @app.get("/internal/v1/market/snapshot", dependencies=auth, response_model=MarketSnapshot)
    def get_market():
        return snapshot_for_research()

    @app.get("/internal/v1/market/freshness", dependencies=auth, response_model=MarketFreshness)
    def get_market_freshness():
        return market_cache.read()

    @app.get("/internal/v1/market/evidence", dependencies=auth, response_model=ToolMarketSnapshot)
    def get_market_evidence():
        snapshot = snapshot_for_research()
        return ToolMarketSnapshot(snapshot=snapshot, data_snapshot_hash=snapshot_hash(snapshot))

    @app.get("/internal/v1/market/indicators", dependencies=auth)
    def get_indicators():
        return indicators(snapshot_for_research(), calendar)

    @app.post("/internal/v1/evaluation-reports", dependencies=auth)
    def evaluate(body: EvaluationRequest, owner: Annotated[UUID, Depends(owner_id)]):
        if body.data_mode != settings.data_mode:
            raise DomainError("DATA_MODE_MISMATCH", "Report does not match configured environment")
        return create_report(store(), owner, body)

    @app.get("/internal/v1/evaluation-reports", dependencies=auth)
    def evaluation_reports(
        owner: Annotated[UUID, Depends(owner_id)],
        cursor: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
    ):
        return list_reports(store(), owner, settings.data_mode, cursor=cursor, limit=limit)

    @app.get("/internal/v1/evaluation-reports/{report_id}", dependencies=auth)
    def evaluation_report(report_id: UUID, owner: Annotated[UUID, Depends(owner_id)]):
        saved = get_report(store(), owner, report_id)
        if saved["data_mode"] != settings.data_mode.value:
            raise DomainError("NOT_FOUND", "Evaluation report not found", status=404)
        return saved

    @app.post("/internal/v1/prediction-drafts", dependencies=auth)
    def create_draft(body: DraftRequest, owner: Annotated[UUID, Depends(owner_id)]):
        snapshot = snapshot_for_research()
        now = snapshot.as_of if snapshot.data_mode == DataMode.REPLAY else clock()
        return store().create_draft(owner, snapshot, now, body.request_id)

    @app.get("/internal/v1/prediction-drafts/{draft_id}", dependencies=auth)
    def get_draft(draft_id: UUID, owner: Annotated[UUID, Depends(owner_id)]):
        saved = store().get_draft(owner, draft_id)
        fact_clock(saved)
        return saved

    @app.get(
        "/internal/v1/prediction-drafts/{draft_id}/evidence",
        dependencies=auth,
        response_model=ToolMarketSnapshot,
    )
    def draft_evidence(draft_id: UUID, owner: Annotated[UUID, Depends(owner_id)]):
        return store().draft_evidence(owner, draft_id, settings.data_mode)

    @app.post("/internal/v1/predictions", dependencies=auth)
    def freeze_prediction(
        body: FreezeRequest,
        idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
        owner: Annotated[UUID, Depends(owner_id)],
    ):
        repository = store()
        saved = repository.get_draft(owner, body.draft_id)
        return repository.freeze(
            owner, body.draft_id, idempotency_key, confirmed=body.confirmed, now=fact_clock(saved)
        )

    @app.post("/internal/v1/predictions/{prediction_id}/corrections", dependencies=auth)
    def correct_prediction(
        prediction_id: UUID,
        body: FreezeRequest,
        idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
        owner: Annotated[UUID, Depends(owner_id)],
    ):
        repository = store()
        fact_clock(repository.get_prediction(owner, prediction_id))
        saved = repository.get_draft(owner, body.draft_id)
        return repository.freeze(
            owner,
            body.draft_id,
            idempotency_key,
            confirmed=body.confirmed,
            now=fact_clock(saved),
            supersedes_prediction_id=prediction_id,
        )

    @app.get("/internal/v1/predictions", dependencies=auth)
    def list_predictions(
        owner: Annotated[UUID, Depends(owner_id)],
        cursor: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        status: Literal["FROZEN", "SETTLED", "UNSCORABLE", "WAITING_DATA", "SETTLEMENT_FAILED"]
        | None = None,
    ):
        return store().prediction_page(
            owner, data_mode=settings.data_mode.value, cursor=cursor, limit=limit, status=status
        )

    @app.get("/internal/v1/predictions/{prediction_id}", dependencies=auth)
    def get_prediction(prediction_id: UUID, owner: Annotated[UUID, Depends(owner_id)]):
        saved = store().get_prediction(owner, prediction_id)
        fact_clock(saved)
        return saved

    @app.post("/internal/v1/replay/predictions/{prediction_id}/settle", dependencies=auth)
    def replay_settle(prediction_id: UUID, owner: Annotated[UUID, Depends(owner_id)]):
        if settings.data_mode != DataMode.REPLAY or settings.env not in ("development", "test"):
            raise DomainError("REPLAY_DISABLED", "Replay settlement is disabled", status=404)
        repository = store()
        fact_clock(repository.get_prediction(owner, prediction_id))
        observed = replay_observation(calendar)
        return repository.settle_prediction(owner, prediction_id, observed, now=observed.as_of)

    # FastAPI's default /openapi.json exposes only schemas, not runtime secrets.
    return app
