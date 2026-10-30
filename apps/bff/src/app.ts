import {trainingEvidenceQuerySchema,trainingEvidenceIdSchema} from "@yta/contracts";
import {backtestIdSchema,backtestQuerySchema} from "@yta/contracts";
import "reflect-metadata";
import { setTimeout as sleep } from "node:timers/promises";
import { Body, Controller, Get, HttpCode, Inject, Module, Param, Post, Query, Req, Res } from "@nestjs/common";
import { NestFactory } from "@nestjs/core";
import {
  createSessionSchema, evaluationRequestSchema, freezeRequestSchema, sendMessageSchema,
  resourceIdSchema as uuid, stockSymbolSchema, idempotencyKeySchema, emptyRequestSchema,
  qualityGateIdSchema, qualityGateRequestSchema, probabilityPolicyQuerySchema, probabilityPolicyIdSchema, probabilityAssessmentIdSchema, probabilityAssessmentRequestSchema, experimentalCalibrationIdSchema, modelReviewSubmissionSchema, qualityPolicyQuerySchema, qualityPolicyIdSchema, qualityAssessmentIdSchema, qualityAssessmentRequestSchema, modelProposalQuerySchema, modelProposalKeySchema, modelProposalRequestSchema, experimentalComparisonRequestSchema, experimentalModelIdSchema, experimentalEvaluationQuerySchema, experimentalEvaluationIdSchema, experimentalModelQuerySchema, pageQuerySchema, predictionQuerySchema, messageQuerySchema, eventQuerySchema,
} from "@yta/contracts";
import type { Request, Response } from "express";
import type { Pool } from "pg";
import { AlphaClient } from "./alpha.js";
import { Auth } from "./auth.js";
import { readConfig, type Config } from "./config.js";
import { database } from "./database.js";
import { readiness } from "./readiness.js";
import { ApiError, ErrorFilter } from "./errors.js";
import { parseJsonBody } from "./request-body.js";
import { documentationRouter } from "./documentation.js";
import { ResearchRepository, terminalTypes } from "./research-store.js";
import { ResearchRunner, type ResearchDriver } from "./research-runner.js";
import { configuredDriver } from "./configured-driver.js";

import { observeRequests, recordResearchAdmission, type LogSink } from "./observability.js";

const SERVICES = Symbol("yta.services");
interface Services { runtime: "hermes" | "replay-fixture" | "unverified"; config: Config; auth: Auth; alpha: AlphaClient; research: ResearchRepository; runner: ResearchRunner; pool: Pool; }
const key = (req: Request) => idempotencyKeySchema.parse(req.headers["idempotency-key"]);
const emptyBody = (body: unknown) => emptyRequestSchema.parse(body ?? {});

@Controller("api/yta/v1")
class ApiController {
  private streams = new Map<string, number>();
  constructor(@Inject(SERVICES) private services: Services) {}

  private requireWriteAdmission() {
    if (this.services.config.YTA_WRITE_ADMISSION === "PAUSED") {
      throw new ApiError("WRITE_ADMISSION_PAUSED", "写入入口暂时关闭，请稍后再试。历史记录和取消操作仍可使用。", 503);
    }
  }

  @Get("health") health() { return { status: "ok", service: "yta-bff", data_mode: this.services.config.YTA_DATA_MODE }; }

  @Get("ready") async ready(@Res({ passthrough: true }) response: Response) {
    const { pool, alpha, config } = this.services;
    const result = await readiness(pool, alpha, config);
    response.status(result.status === "ready" ? 200 : 503);
    return result;
  }

  @Post("auth/login") @HttpCode(200)
  login(@Req() req: Request, @Res({ passthrough: true }) res: Response, @Body() body: unknown) { return this.services.auth.login(req, res, body); }

  @Post("auth/logout") @HttpCode(200)
  logout(@Req() req: Request, @Res({ passthrough: true }) res: Response, @Body() body: unknown) {
    emptyBody(body);
    return this.services.auth.logout(req, res);
  }

  @Get("me") async me(@Req() req: Request) {
    const identity = await this.services.auth.require(req);
    return { user: { id: identity.ownerId, email: identity.email }, csrf_token: identity.csrfToken, data_mode: this.services.config.YTA_DATA_MODE, experimental_models_enabled: this.services.config.YTA_EXPERIMENTAL_MODELS === "ENABLED", can_review_models: this.services.auth.canReviewModels(identity), runtime: this.services.runtime };
  }

  @Get("profiles") async profiles(@Req() req: Request) {
    await this.services.auth.require(req);
    return { items: [{ id: "alpha-orchestrator", name: "研究协调员" }] };
  }

  @Post("sessions") async createSession(@Req() req: Request, @Body() body: unknown) {
    const user = await this.services.auth.mutation(req);
    this.requireWriteAdmission();
    createSessionSchema.parse(body);
    return this.services.research.createSession(user.ownerId, this.services.config.YTA_DATA_MODE, key(req));
  }

  @Get("sessions") async sessions(@Req() req: Request, @Query() query: unknown) {
    const user = await this.services.auth.require(req);
    const input = pageQuerySchema.parse(query);
    return this.services.research.listSessions(user.ownerId, this.services.config.YTA_DATA_MODE, input.cursor, input.limit);
  }

  @Post("sessions/:id/messages") @HttpCode(202)
  async message(@Req() req: Request, @Param("id") id: string, @Body() body: unknown) {
    const user = await this.services.auth.mutation(req);
    this.requireWriteAdmission();
    const input = sendMessageSchema.parse(body);
    const suppliedKey = req.headers["idempotency-key"];
    if (suppliedKey !== undefined && suppliedKey !== input.request_id) throw new ApiError("IDEMPOTENCY_CONFLICT", "Header key must match message request_id", 409);
    const result = await this.services.research.submit(user.ownerId, uuid.parse(id), input.request_id, input.text, this.services.config.YTA_DATA_MODE);
    recordResearchAdmission(result.run_id);
    if (result.created) this.services.runner.launch(await this.services.research.getRun(user.ownerId, result.run_id));
    return { run_id: result.run_id };
  }

  @Get("sessions/:id/messages") async messages(@Req() req: Request, @Param("id") id: string, @Query() query: unknown) {
    const user = await this.services.auth.require(req);
    const {cursor: after} = messageQuerySchema.parse(query);
    return this.services.research.messages(user.ownerId, uuid.parse(id), after);
  }

  @Get("runs/:id") async run(@Req() req: Request, @Param("id") id: string) {
    const user = await this.services.auth.require(req);
    return this.services.research.runSummary(user.ownerId, uuid.parse(id));
  }

  @Post("runs/:id/cancel") @HttpCode(202)
  async cancel(@Req() req: Request, @Param("id") id: string, @Body() body: unknown) {
    const user = await this.services.auth.mutation(req);
    emptyBody(body); key(req);
    return this.services.research.cancel(user.ownerId, uuid.parse(id));
  }

  @Get("runs/:id/events") async events(@Req() req: Request, @Res() res: Response, @Param("id") id: string, @Query() query: unknown) {
    const { auth, research } = this.services;
    const user = await auth.require(req);
    const runId = uuid.parse(id);
    const input = eventQuerySchema.parse(query);
    const cursor = uuid.optional().parse(req.headers["last-event-id"] ?? input.cursor);
    let sequence = await research.cursor(user.ownerId, runId, cursor);
    const count = this.streams.get(user.ownerId) ?? 0;
    if (count >= 4) throw new ApiError("STREAM_LIMIT", "Too many open event streams", 429, true);
    this.streams.set(user.ownerId, count + 1);
    let closed = false;
    const close = () => { closed = true; };
    res.on("close", close);
    res.setHeader("Content-Type", "text/event-stream; charset=utf-8");
    res.setHeader("Cache-Control", "no-cache, no-transform");
    res.setHeader("X-Accel-Buffering", "no");
    res.flushHeaders();
    const write = async (text: string) => {
      if (closed) return;
      if (!res.write(text)) {
        await new Promise<void>((resolve, reject) => {
          const cleanup = () => { clearTimeout(timer); res.off("drain", drained); res.off("close", drained); };
          const drained = () => { cleanup(); resolve(); };
          const timer = setTimeout(() => { cleanup(); reject(new Error("Slow SSE consumer")); }, 10000);
          res.once("drain", drained); res.once("close", drained);
        });
      }
    };
    let heartbeat = Date.now();
    let authenticated = Date.now();
    try {
      await write("retry: 1500\n\n");
      while (!closed) {
        if (Date.now() - authenticated > 2000) { await auth.require(req); authenticated = Date.now(); }
        const batch = await research.events(user.ownerId, runId, sequence);
        for (const event of batch) {
          await write(`id: ${event.event_id}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`);
          sequence = event.sequence;
          if (terminalTypes.includes(event.type)) return;
        }
        if (!batch.length) {
          const summary = await research.runSummary(user.ownerId, runId);
          if (["COMPLETED", "FAILED", "CANCELED", "EXECUTION_UNKNOWN"].includes(summary.status)) return;
        }
        if (Date.now() - heartbeat > 10000) { await write(": heartbeat\n\n"); heartbeat = Date.now(); }
        await sleep(150);
      }
    } catch { /* A disconnected/unauthenticated stream never cancels or resubmits the run. */ }
    finally {
      res.off("close", close);
      const remaining = (this.streams.get(user.ownerId) ?? 1) - 1;
      if (remaining) this.streams.set(user.ownerId, remaining); else this.streams.delete(user.ownerId);
      res.end();
    }
  }

  @Get("stocks/:symbol/bars") async bars(@Req() req: Request, @Param("symbol") symbol: string) {
    const user = await this.services.auth.require(req);
    stockSymbolSchema.parse(symbol);
    return this.services.alpha.request("/internal/v1/market/snapshot", user.ownerId);
  }

  @Get("stocks/:symbol/freshness") async freshness(@Req() req: Request, @Param("symbol") symbol: string) {
    const user = await this.services.auth.require(req);
    stockSymbolSchema.parse(symbol);
    return this.services.alpha.marketFreshness(user.ownerId);
  }

  @Get("stocks/:symbol/indicators") async indicators(@Req() req: Request, @Param("symbol") symbol: string) {
    const user = await this.services.auth.require(req);
    stockSymbolSchema.parse(symbol);
    return this.services.alpha.request("/internal/v1/market/indicators", user.ownerId);
  }

  @Get("prediction-drafts/:id") async draft(@Req() req: Request, @Param("id") id: string) {
    const user = await this.services.auth.require(req);
    return this.services.alpha.request(`/internal/v1/prediction-drafts/${uuid.parse(id)}`, user.ownerId);
  }

  @Get("prediction-drafts/:id/evidence") async draftEvidence(@Req() req: Request, @Param("id") id: string) {
    const user = await this.services.auth.require(req);
    return this.services.alpha.draftEvidence(user.ownerId, uuid.parse(id));
  }

  @Post("predictions") @HttpCode(200)
  async freeze(@Req() req: Request, @Body() body: unknown) {
    const user = await this.services.auth.mutation(req);
    this.requireWriteAdmission();
    return this.services.alpha.request("/internal/v1/predictions", user.ownerId, freezeRequestSchema.parse(body), key(req));
  }

  @Post("evaluation-reports") @HttpCode(200)
  async evaluate(@Req() req: Request, @Body() body: unknown) {
    const user = await this.services.auth.mutation(req);
    this.requireWriteAdmission();
    const input = evaluationRequestSchema.parse(body);
    return this.services.alpha.request("/internal/v1/evaluation-reports", user.ownerId,
      { ...input, data_mode: this.services.config.YTA_DATA_MODE });
  }

  @Get("experimental-models/:id/evaluations") async experimentalEvaluations(
    @Req() req: Request, @Param("id") id: string, @Query() query: unknown,
  ) {
    const user = await this.services.auth.require(req);
    if (this.services.config.YTA_EXPERIMENTAL_MODELS !== "ENABLED") {
      throw new ApiError("EXPERIMENTAL_MODELS_DISABLED", "实验模型评测尚未启用。", 503);
    }
    return this.services.alpha.experimentalEvaluations(user.ownerId,
      experimentalModelIdSchema.parse(id), experimentalEvaluationQuerySchema.parse(query));
  }

  @Get("experimental-models/:id/training-evidence")
  async trainingEvidenceList(@Req() req:Request,@Param("id") id:string,@Query() query:unknown) {
    const user=await this.services.auth.require(req);
    if(this.services.config.YTA_EXPERIMENTAL_MODELS!=="ENABLED")throw new ApiError("EXPERIMENTAL_MODELS_DISABLED","训练证据尚未启用。",503);
    return this.services.alpha.trainingEvidenceList(user.ownerId,experimentalModelIdSchema.parse(id),trainingEvidenceQuerySchema.parse(query));
  }
  @Get("training-evidence/:id")
  async trainingEvidence(@Req() req:Request,@Param("id") id:string,@Query() query:unknown) {
    const user=await this.services.auth.require(req);
    if(this.services.config.YTA_EXPERIMENTAL_MODELS!=="ENABLED")throw new ApiError("EXPERIMENTAL_MODELS_DISABLED","训练证据尚未启用。",503);
    emptyRequestSchema.parse(query);
    return this.services.alpha.trainingEvidence(user.ownerId,trainingEvidenceIdSchema.parse(id));
  }
  @Get("backtest-reports")
  async backtestReports(@Req() req:Request,@Query() query:unknown) {
    const user=await this.services.auth.require(req);
    if(this.services.config.YTA_EXPERIMENTAL_MODELS!=="ENABLED")throw new ApiError("EXPERIMENTAL_MODELS_DISABLED","回测报告尚未启用。",503);
    return this.services.alpha.backtestReports(user.ownerId,backtestQuerySchema.parse(query));
  }
  @Get("backtest-reports/:id")
  async backtestReport(@Req() req:Request,@Param("id") id:string,@Query() query:unknown) {
    const user=await this.services.auth.require(req);
    if(this.services.config.YTA_EXPERIMENTAL_MODELS!=="ENABLED")throw new ApiError("EXPERIMENTAL_MODELS_DISABLED","回测报告尚未启用。",503);
    emptyRequestSchema.parse(query);
    return this.services.alpha.backtestReport(user.ownerId,backtestIdSchema.parse(id));
  }
  @Get("experimental-calibrations/:id")
  async calibrationReport(@Req() req:Request,@Param("id") id:string,@Query() query:unknown) {
    const user=await this.services.auth.require(req);
    if(this.services.config.YTA_EXPERIMENTAL_MODELS!=="ENABLED")throw new ApiError("EXPERIMENTAL_MODELS_DISABLED","概率校准报告尚未启用。",503);
    emptyRequestSchema.parse(query);
    return this.services.alpha.calibrationReport(user.ownerId,experimentalCalibrationIdSchema.parse(id));
  }
  @Post("model-proposals") @HttpCode(200)
  async submitModelProposal(@Req() req: Request, @Body() body: unknown, @Query() query: unknown) {
    const user = await this.services.auth.mutation(req);
    this.requireWriteAdmission();
    if (this.services.config.YTA_EXPERIMENTAL_MODELS !== "ENABLED") {
      throw new ApiError("EXPERIMENTAL_MODELS_DISABLED", "实验模型提案尚未启用。", 503);
    }
    emptyRequestSchema.parse(query);
    return this.services.alpha.submitModelProposal(user.ownerId, modelProposalRequestSchema.parse(body),
      modelProposalKeySchema.parse(req.headers["idempotency-key"]));
  }

  @Post("model-reviews") @HttpCode(200)
  async submitModelReview(@Req() req: Request,@Body() body: unknown,@Query() query: unknown) {
    const user=await this.services.auth.modelReviewMutation(req);this.requireWriteAdmission();emptyRequestSchema.parse(query);
    return this.services.alpha.submitModelReview(user.ownerId,modelReviewSubmissionSchema.parse(body),modelProposalKeySchema.parse(req.headers["idempotency-key"]));
  }
  @Post("model-review-quality-gates") @HttpCode(200)
  async createReviewQualityGate(@Req() req:Request,@Body() body:unknown,@Query() query:unknown) {
    const reviewer=await this.services.auth.modelReviewMutation(req);this.requireWriteAdmission();emptyRequestSchema.parse(query);
    return this.services.alpha.createReviewQualityGate(reviewer.ownerId,qualityGateRequestSchema.parse(body));
  }
  @Get("model-review-quality-gates/:id")
  async reviewQualityGate(@Req() req:Request,@Param("id") id:string,@Query() query:unknown) {
    const reviewer=await this.services.auth.requireModelReviewer(req);emptyRequestSchema.parse(query);
    return this.services.alpha.reviewQualityGate(reviewer.ownerId,qualityGateIdSchema.parse(id));
  }
  @Get("model-review-proposals")
  async reviewProposals(@Req() req:Request,@Query() query:unknown) {
    const reviewer=await this.services.auth.requireModelReviewer(req);
    return this.services.alpha.reviewProposals(reviewer.ownerId,modelProposalQuerySchema.parse(query));
  }
  @Get("model-review-proposals/:id")
  async reviewProposal(@Req() req:Request,@Param("id") id:string,@Query() query:unknown) {
    const reviewer=await this.services.auth.requireModelReviewer(req);emptyRequestSchema.parse(query);
    return this.services.alpha.reviewProposal(reviewer.ownerId,modelProposalKeySchema.parse(id));
  }
  @Get("model-proposals/:id/reviews")
  async modelReviewHistory(@Req() req: Request,@Param("id") id:string,@Query() query:unknown) {
    const user=await this.services.auth.requireModelReviewer(req);
    return this.services.alpha.modelReviewHistory(user.ownerId,modelProposalKeySchema.parse(id),modelProposalQuerySchema.parse(query));
  }
  @Get("model-reviews/:id") async modelReview(@Req() req: Request,@Param("id") id: string,@Query() query: unknown) {
    const user=await this.services.auth.requireModelReviewer(req);emptyRequestSchema.parse(query);
    return this.services.alpha.modelReview(user.ownerId,modelProposalKeySchema.parse(id));
  }

  private requireQualityEnabled() {
    if(this.services.config.YTA_EXPERIMENTAL_MODELS!=="ENABLED") {
      throw new ApiError("EXPERIMENTAL_MODELS_DISABLED","历史质量核验尚未启用。",503);
    }
  }
  @Get("historical-quality-policies") async qualityPolicies(@Req() req: Request,@Query() query: unknown) {
    const user=await this.services.auth.require(req);this.requireQualityEnabled();
    return this.services.alpha.qualityPolicies(user.ownerId,qualityPolicyQuerySchema.parse(query));
  }
  @Get("historical-quality-policies/:id") async qualityPolicy(@Req() req: Request,@Param("id") id: string,@Query() query: unknown) {
    const user=await this.services.auth.require(req);this.requireQualityEnabled();emptyRequestSchema.parse(query);
    return this.services.alpha.qualityPolicy(user.ownerId,qualityPolicyIdSchema.parse(id));
  }
  @Get("historical-quality-assessments/:id") async qualityAssessment(@Req() req: Request,@Param("id") id: string,@Query() query: unknown) {
    const user=await this.services.auth.require(req);this.requireQualityEnabled();emptyRequestSchema.parse(query);
    return this.services.alpha.qualityAssessment(user.ownerId,qualityAssessmentIdSchema.parse(id));
  }
  @Post("historical-quality-assessments") @HttpCode(200)
  async assessQuality(@Req() req: Request,@Body() body: unknown,@Query() query: unknown) {
    const user=await this.services.auth.mutation(req);this.requireWriteAdmission();this.requireQualityEnabled();emptyRequestSchema.parse(query);
    return this.services.alpha.assessQuality(user.ownerId,qualityAssessmentRequestSchema.parse(body));
  }
  @Get("model-quality-gates/:id") async qualityGate(@Req() req:Request,@Param("id") id:string,@Query() query:unknown) {
    const user=await this.services.auth.require(req);this.requireQualityEnabled();emptyRequestSchema.parse(query);
    return this.services.alpha.qualityGate(user.ownerId,qualityGateIdSchema.parse(id));
  }
  @Post("model-quality-gates") @HttpCode(200)
  async createQualityGate(@Req() req:Request,@Body() body:unknown,@Query() query:unknown) {
    const user=await this.services.auth.mutation(req);this.requireWriteAdmission();this.requireQualityEnabled();emptyRequestSchema.parse(query);
    return this.services.alpha.createQualityGate(user.ownerId,qualityGateRequestSchema.parse(body));
  }
  @Get("probability-quality-policies") async probabilityPolicies(@Req() req: Request,@Query() query: unknown) {
    const user=await this.services.auth.require(req);this.requireQualityEnabled();
    return this.services.alpha.probabilityPolicies(user.ownerId,probabilityPolicyQuerySchema.parse(query));
  }
  @Get("probability-quality-policies/:id") async probabilityPolicy(@Req() req: Request,@Param("id") id: string,@Query() query: unknown) {
    const user=await this.services.auth.require(req);this.requireQualityEnabled();emptyRequestSchema.parse(query);
    return this.services.alpha.probabilityPolicy(user.ownerId,probabilityPolicyIdSchema.parse(id));
  }
  @Get("probability-quality-assessments/:id") async probabilityAssessment(@Req() req: Request,@Param("id") id: string,@Query() query: unknown) {
    const user=await this.services.auth.require(req);this.requireQualityEnabled();emptyRequestSchema.parse(query);
    return this.services.alpha.probabilityAssessment(user.ownerId,probabilityAssessmentIdSchema.parse(id));
  }
  @Post("probability-quality-assessments") @HttpCode(200)
  async assessProbability(@Req() req: Request,@Body() body: unknown,@Query() query: unknown) {
    const user=await this.services.auth.mutation(req);this.requireWriteAdmission();this.requireQualityEnabled();emptyRequestSchema.parse(query);
    return this.services.alpha.assessProbability(user.ownerId,probabilityAssessmentRequestSchema.parse(body));
  }

  @Get("model-proposals") async modelProposals(@Req() req: Request, @Query() query: unknown) {
    const user = await this.services.auth.require(req);
    if (this.services.config.YTA_EXPERIMENTAL_MODELS !== "ENABLED") {
      throw new ApiError("EXPERIMENTAL_MODELS_DISABLED", "实验模型提案尚未启用。", 503);
    }
    return this.services.alpha.modelProposals(user.ownerId, modelProposalQuerySchema.parse(query));
  }

  @Get("model-proposals/:id") async modelProposal(@Req() req: Request, @Param("id") id: string, @Query() query: unknown) {
    const user = await this.services.auth.require(req);
    if (this.services.config.YTA_EXPERIMENTAL_MODELS !== "ENABLED") {
      throw new ApiError("EXPERIMENTAL_MODELS_DISABLED", "实验模型提案尚未启用。", 503);
    }
    emptyRequestSchema.parse(query);
    return this.services.alpha.modelProposal(user.ownerId, uuid.parse(id));
  }

  @Post("experimental-evaluations/compare") @HttpCode(200)
  async experimentalComparison(@Req() req: Request, @Body() body: unknown, @Query() query: unknown) {
    const user = await this.services.auth.mutation(req);
    if (this.services.config.YTA_EXPERIMENTAL_MODELS !== "ENABLED") {
      throw new ApiError("EXPERIMENTAL_MODELS_DISABLED", "实验模型评测尚未启用。", 503);
    }
    emptyRequestSchema.parse(query);
    return this.services.alpha.experimentalComparison(user.ownerId, experimentalComparisonRequestSchema.parse(body));
  }

  @Get("experimental-evaluations/:id") async experimentalEvaluation(
    @Req() req: Request, @Param("id") id: string, @Query() query: unknown,
  ) {
    const user = await this.services.auth.require(req);
    if (this.services.config.YTA_EXPERIMENTAL_MODELS !== "ENABLED") {
      throw new ApiError("EXPERIMENTAL_MODELS_DISABLED", "实验模型评测尚未启用。", 503);
    }
    emptyRequestSchema.parse(query);
    return this.services.alpha.experimentalEvaluation(user.ownerId, experimentalEvaluationIdSchema.parse(id));
  }

  @Get("experimental-models") async experimentalModels(@Req() req: Request, @Query() query: unknown) {
    const user = await this.services.auth.require(req);
    if (this.services.config.YTA_EXPERIMENTAL_MODELS !== "ENABLED") {
      throw new ApiError("EXPERIMENTAL_MODELS_DISABLED", "实验模型目录尚未启用。", 503);
    }
    return this.services.alpha.experimentalModels(user.ownerId, experimentalModelQuerySchema.parse(query));
  }

  @Get("evaluation-reports") async evaluationReports(@Req() req: Request, @Query() query: unknown) {
    const user = await this.services.auth.require(req);
    const input = pageQuerySchema.parse(query);
    const params = new URLSearchParams();
    if (input.cursor) params.set("cursor", input.cursor);
    if (input.limit !== undefined) params.set("limit", String(input.limit));
    return this.services.alpha.request("/internal/v1/evaluation-reports?" + params.toString(), user.ownerId);
  }

  @Get("evaluation-reports/:id") async evaluationReport(@Req() req: Request, @Param("id") id: string) {
    const user = await this.services.auth.require(req);
    return this.services.alpha.request(`/internal/v1/evaluation-reports/${uuid.parse(id)}`, user.ownerId);
  }

  @Post("predictions/:id/corrections") @HttpCode(200)
  async correctPrediction(@Req() req: Request, @Param("id") id: string, @Body() body: unknown) {
    const user = await this.services.auth.mutation(req);
    this.requireWriteAdmission();
    return this.services.alpha.request("/internal/v1/predictions/" + uuid.parse(id) + "/corrections",
      user.ownerId, freezeRequestSchema.parse(body), key(req));
  }

  @Get("predictions") async predictions(@Req() req: Request, @Query() query: unknown) {
    const user = await this.services.auth.require(req);
    const input = predictionQuerySchema.parse(query);
    const params = new URLSearchParams();
    if (input.cursor) params.set("cursor", input.cursor);
    if (input.limit !== undefined) params.set("limit", String(input.limit));
    if (input.status) params.set("status", input.status);
    return this.services.alpha.request("/internal/v1/predictions" + (params.size ? "?" + params.toString() : ""), user.ownerId);
  }

  @Get("predictions/:id") async prediction(@Req() req: Request, @Param("id") id: string) {
    const user = await this.services.auth.require(req);
    return this.services.alpha.request(`/internal/v1/predictions/${uuid.parse(id)}`, user.ownerId);
  }

  @Post("replay/predictions/:id/settle") @HttpCode(200)
  async settleReplay(@Req() req: Request, @Param("id") id: string, @Body() body: unknown) {
    const user = await this.services.auth.mutation(req);
    this.requireWriteAdmission();
    emptyBody(body); key(req);
    if (this.services.config.YTA_DATA_MODE !== "REPLAY") throw new ApiError("REPLAY_DISABLED", "Replay is disabled", 404);
    return this.services.alpha.request(`/internal/v1/replay/predictions/${uuid.parse(id)}/settle`, user.ownerId, {});
  }
}

export async function createBff(options: { config?: Config; pool?: Pool; driver?: ResearchDriver; startRunner?: boolean; log?: LogSink } = {}) {
  const config = options.config ?? readConfig();
  const pool = options.pool ?? database(config.YTA_DATABASE_URL);
  const research = new ResearchRepository(pool, config.YTA_DATA_MODE, config.YTA_RESEARCH_RUNS_PER_24H);
  const alpha = new AlphaClient(config);
  const auth = new Auth(pool, config);
  const driver: ResearchDriver = options.driver ?? configuredDriver(config, pool, alpha);
  const runner = new ResearchRunner(research, driver, config);
  @Module({ controllers: [ApiController], providers: [{ provide: SERVICES, useValue: { config, pool, research, alpha, auth, runner, runtime: driver.runtime ?? (config.YTA_DATA_MODE === "REPLAY" ? "replay-fixture" : "unverified") } satisfies Services }] })
  class BffModule {}
  const app = await NestFactory.create(BffModule, { logger: false, bodyParser: false });
  app.use(observeRequests(options.log));
  app.use((req: Request, res: Response, next: () => void) => {
    res.setHeader("Cache-Control", "no-store");
    res.setHeader("X-Content-Type-Options", "nosniff");
    res.setHeader("Referrer-Policy", "no-referrer");
    res.removeHeader("X-Powered-By");
    next();
  });
  app.use(parseJsonBody);
  app.use(documentationRouter(auth));
  app.useGlobalFilters(new ErrorFilter());
  await app.init();
  if (options.startRunner !== false) await runner.start();
  return { app, pool, research, runner, config, close: async () => { await runner.close(); await app.close(); if (!options.pool) await pool.end(); } };
}
