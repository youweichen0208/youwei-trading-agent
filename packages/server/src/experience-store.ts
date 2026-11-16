import {
  awaitTaskVerification,
  requireVerifiedTask,
} from "./task-verification.js";
import { randomUUID } from "node:crypto";
import type { Pool } from "pg";
import { z } from "zod";
import { symbol, uuid } from "./business.js";
import { DomainError } from "./tasks.js";
import type { ToolContext } from "./data-service.js";
import {
  assertToolWrite,
  canonical,
  sha256,
  toolTransaction,
} from "./tool-context.js";
import { transaction, lockOwner } from "./db.js";
import { QuantRunner } from "./quant-runner.js";
import type { MarketProvider } from "../../market/src/alpaca.js";
import {
  executionFingerprint,
  forecastDistribution,
  frozenContext,
  settleSnapshot,
} from "./forecast-store.js";

const lessonInput = z
  .object({
    title: z.string().trim().min(1).max(200),
    statement: z.string().trim().min(1).max(5000),
    scope: z.enum(["company", "industry", "general"]),
    symbol: symbol.optional(),
    horizon: z.union([z.literal(5), z.literal(20)]),
    eventType: z.string().trim().min(1).max(200),
    applicability: z.string().trim().min(1).max(4000),
    invalidation: z.string().trim().min(1).max(4000),
  })
  .strict()
  .refine((v) => v.scope !== "company" || !!v.symbol);
export const reviewInput = z
  .object({
    forecastId: uuid,
    text: z.string().trim().min(1).max(16000),
    attribution: z
      .enum(["supported", "hypothesis", "unresolved"])
      .default("hypothesis"),
    lesson: lessonInput.optional(),
  })
  .strict();
export const forecastTrialArmInput = z
  .object({
    trialId: uuid,
    arm: z.enum(["control", "candidate"]),
    distribution: forecastDistribution,
  })
  .strict();

export class ExperienceStore {
  constructor(
    readonly pool: Pool,
    private quant = new QuantRunner(),
    private clock = () => new Date(),
  ) {}
  async recordReview(ctx: ToolContext, input: unknown, key: string) {
    const request = reviewInput.parse(input),
      hash = sha256(canonical(request));
    return awaitTaskVerification(() =>
      toolTransaction(this.pool, ctx, async (c, t) => {
        const old = (
          await c.query(
            "SELECT id,result_id,request_hash FROM forecast_reviews WHERE task_id=$1 AND operation_key=$2",
            [t.id, key],
          )
        ).rows[0];
        if (old) {
          if (old.request_hash !== hash)
            throw new DomainError("operation_key_conflict");
          const lesson = (
            await c.query(
              "SELECT lesson_id,id FROM experience_versions WHERE review_id=$1",
              [old.id],
            )
          ).rows[0];
          return {
            reviewId: old.id,
            resultRef: old.result_id,
            lessonId: lesson?.lesson_id,
            lessonVersionRef: lesson?.id,
          };
        }
        requireVerifiedTask(t);
        assertToolWrite(t);
        if (t.role !== "research")
          throw new DomainError("tool_not_allowed", 403);
        const forecast = (
          await c.query(
            "SELECT f.*,e.result_id AS evaluation_ref,e.payload AS evaluation FROM forecasts f JOIN forecast_evaluations e ON e.forecast_id=f.id WHERE f.id=$1 AND f.owner_id=$2",
            [request.forecastId, ctx.ownerId],
          )
        ).rows[0];
        if (!forecast) throw new DomainError("resolved_forecast_required", 422);
        const id = randomUUID(),
          resultId = randomUUID(),
          payload = {
            ...request,
            evaluation: forecast.evaluation,
            attributionIsHypothesis: request.attribution !== "supported",
          };
        await c.query(
          "INSERT INTO results(id,owner_id,task_id,kind,payload,lineage) VALUES($1,$2,$3,'review',$4,$5)",
          [
            resultId,
            ctx.ownerId,
            t.id,
            JSON.stringify(payload),
            JSON.stringify({
              forecastId: forecast.id,
              evaluationRef: forecast.evaluation_ref,
            }),
          ],
        );
        await c.query(
          "INSERT INTO forecast_reviews(id,forecast_id,owner_id,task_id,result_id,operation_key,request_hash,payload) VALUES($1,$2,$3,$4,$5,$6,$7,$8)",
          [
            id,
            forecast.id,
            ctx.ownerId,
            t.id,
            resultId,
            key,
            hash,
            JSON.stringify(payload),
          ],
        );
        let lessonId: string | undefined, lessonVersionRef: string | undefined;
        if (request.lesson) {
          const l = request.lesson;
          lessonId = randomUUID();
          lessonVersionRef = randomUUID();
          await c.query(
            "INSERT INTO experience_lessons(id,owner_id,title,scope,status) VALUES($1,$2,$3,$4,'shadow')",
            [lessonId, ctx.ownerId, l.title, l.scope],
          );
          await c.query(
            "INSERT INTO experience_versions(id,owner_id,lesson_id,review_id,horizon,symbol,event_type,statement,applicability,invalidation) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
            [
              lessonVersionRef,
              ctx.ownerId,
              lessonId,
              id,
              l.horizon,
              l.symbol ?? null,
              l.eventType,
              l.statement,
              l.applicability,
              l.invalidation,
            ],
          );
          await c.query(
            "UPDATE experience_lessons SET current_version_id=$2 WHERE id=$1",
            [lessonId, lessonVersionRef],
          );
        }
        return {
          reviewId: id,
          resultRef: resultId,
          lessonId,
          lessonVersionRef,
        };
      }),
    );
  }
  /** `owner` null lists the Hermes agent's shared lessons across accounts. */
  async list(owner: string | null) {
    return (
      await this.pool.query(
        `SELECT l.*,v.horizon,v.symbol,v.event_type,v.statement,v.applicability,v.invalidation,
      (SELECT count(*)::integer FROM experience_trials t WHERE t.lesson_version_id=v.id) AS trial_count,
      (SELECT payload FROM experience_batches b WHERE b.lesson_version_id=v.id ORDER BY created_at DESC LIMIT 1) AS last_batch
      FROM experience_lessons l JOIN experience_versions v ON v.id=l.current_version_id${
        owner === null ? "" : " WHERE l.owner_id=$1"
      } ORDER BY l.created_at DESC LIMIT 200`,
        owner === null ? [] : [owner],
      )
    ).rows;
  }
  async getTrialCandidates(owner: string, limit = 1) {
    return (
      await this.pool.query(
        `SELECT v.id AS "lessonVersionRef",c.symbol,v.horizon,
      v.event_type AS "eventType",v.applicability,v.invalidation,l.status,v.created_at,l.scope
      FROM experience_lessons l JOIN experience_versions v ON v.id=l.current_version_id
      JOIN research_coverage c ON c.owner_id=l.owner_id AND NOT c.paused
        AND (l.scope<>'company' OR v.symbol=c.symbol)
      WHERE l.owner_id=$1 AND l.status IN ('shadow','active')
        AND (c.expires_at IS NULL OR c.expires_at>now())
        AND (c.expires_session IS NULL OR c.expires_session>=current_date)
      ORDER BY (SELECT max(t.published_at) FROM experience_trials t WHERE t.lesson_version_id=v.id AND t.symbol=c.symbol) NULLS FIRST,c.priority DESC,v.created_at
      LIMIT $2`,
        [owner, Math.min(20, Math.max(1, limit))],
      )
    ).rows;
  }
  async createTrialAssignment(
    owner: string,
    candidate: { lessonVersionRef: string; symbol: string; horizon: number },
    input: { researchContextRef: string; eventKey: string },
  ) {
    return transaction(this.pool, async (c) => {
      await lockOwner(c, owner);
      const prior = (
        await c.query(
          "SELECT id,payload FROM experience_trials WHERE owner_id=$1 AND lesson_version_id=$2 AND symbol=$3 AND event_key=$4",
          [owner, candidate.lessonVersionRef, candidate.symbol, input.eventKey],
        )
      ).rows[0];
      if (prior) return { trialId: prior.id, ...prior.payload.assignment };
      const now = this.clock(),
        context = await frozenContext(
          c,
          owner,
          input.researchContextRef,
          candidate.symbol,
          now,
        );
      const lesson = (
        await c.query(
          "SELECT v.*,l.title,l.scope,l.status FROM experience_versions v JOIN experience_lessons l ON l.current_version_id=v.id WHERE v.id=$1 AND v.owner_id=$2 AND l.status IN ('shadow','active')",
          [candidate.lessonVersionRef, owner],
        )
      ).rows[0];
      if (
        !lesson ||
        lesson.horizon !== candidate.horizon ||
        (lesson.scope === "company" && lesson.symbol !== candidate.symbol)
      )
        throw new DomainError("lesson_not_applicable", 422);
      if (new Date(context.as_of) < new Date(lesson.created_at))
        throw new DomainError("trial_requires_new_evidence", 422);
      const events = (
        await c.query(
          "SELECT source,source_key,published_at,observed_at FROM research_evidence WHERE owner_id=$1 AND id=ANY($2::uuid[])",
          [owner, context.payload.evidenceRefs ?? []],
        )
      ).rows;
      const event = events.find(
        (e) => sha256(`${e.source}:${e.source_key}`) === input.eventKey,
      );
      if (
        !event ||
        !event.published_at ||
        new Date(event.published_at) < new Date(lesson.created_at) ||
        new Date(event.published_at) > new Date(context.as_of) ||
        new Date(event.observed_at) > new Date(context.as_of)
      )
        throw new DomainError("trial_requires_new_source_event", 422);
      const plan = await this.quant.run({
        operation: "forecast_plan",
        publishedAt: now.toISOString(),
        horizon: candidate.horizon,
      });
      const controlContextRef = randomUUID(),
        candidateContextRef = randomUUID();
      // Do not expose prior forecasts/reviews that may already carry the tested lesson into control.
      const datasets = (
        await c.query(
          "SELECT id,metadata,payload FROM datasets WHERE owner_id=$1 AND id=ANY($2::uuid[]) AND created_at<=$3",
          [owner, context.payload.datasetRefs ?? [], context.as_of],
        )
      ).rows;
      const frozenDatasets = datasets.map((d) => ({
        datasetRef: d.id,
        metadata: d.metadata,
        rows: (d.payload.rows ?? []).slice(-100),
      }));
      const evidenceOnly = {
        symbol: context.payload.symbol,
        asOf: context.payload.asOf,
        evidenceRefs: context.payload.evidenceRefs,
        evidence: context.payload.evidence,
        capabilities: context.payload.capabilities,
        datasetRefs: context.payload.datasetRefs ?? [],
        datasets: frozenDatasets,
      };
      const active = (context.payload.lessons ?? []).filter(
        (l: any) =>
          l.status === "active" &&
          l.version_id !== lesson.id &&
          l.horizon === candidate.horizon,
      );
      const candidateLesson = { ...lesson, version_id: lesson.id };
      for (const [id, lessons] of [
        [controlContextRef, active],
        [candidateContextRef, [...active, candidateLesson]],
      ] as const) {
        await c.query(
          "INSERT INTO research_contexts(id,owner_id,symbol,as_of,payload) VALUES($1,$2,$3,$4,$5)",
          [
            id,
            owner,
            candidate.symbol,
            context.as_of,
            JSON.stringify({ ...evidenceOnly, lessons }),
          ],
        );
      }
      const assignment = {
        controlContextRef,
        candidateContextRef,
        startOpenAt: plan.startOpenAt,
        targetSession: plan.targetSession,
      };
      const payload = {
        assignment,
        ...plan,
        symbol: candidate.symbol,
        horizon: candidate.horizon,
        activeLessonVersionRefs: active.map((l: any) => l.version_id).sort(),
        modelVersion:
          process.env.FORECAST_MODEL_VERSION ?? "pinned-profile-runtime",
        methodVersion: "forward-forecast-v1",
        evidenceHash: sha256(canonical(evidenceOnly)),
        intervalCoverage: "0.8",
        comparisonPolicy:
          "independent-sessions-same-frozen-evidence-add-one-lesson-v2",
      };
      const id = randomUUID();
      await c.query(
        `INSERT INTO experience_trials(id,owner_id,lesson_version_id,research_context_id,symbol,horizon,event_key,operation_key,request_hash,published_at,start_session,target_session,evaluate_after,payload)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)`,
        [
          id,
          owner,
          lesson.id,
          context.id,
          candidate.symbol,
          candidate.horizon,
          input.eventKey,
          id,
          sha256(canonical({ candidate, input })),
          now,
          plan.startSession,
          plan.targetSession,
          plan.evaluateAfter,
          JSON.stringify(payload),
        ],
      );
      return { trialId: id, ...assignment };
    });
  }
  async bindTrialArm(
    owner: string,
    trialId: string,
    arm: "control" | "candidate",
    threadId: string,
  ) {
    return transaction(this.pool, async (c) => {
      await lockOwner(c, owner);
      const trial = (
        await c.query(
          "SELECT id FROM experience_trials WHERE id=$1 AND owner_id=$2",
          [trialId, owner],
        )
      ).rows[0];
      const thread = (
        await c.query("SELECT id FROM threads WHERE id=$1 AND owner_id=$2", [
          threadId,
          owner,
        ])
      ).rows[0];
      if (!trial || !thread) throw new DomainError("not_found", 404);
      const old = (
        await c.query(
          "SELECT thread_id FROM experience_trial_arm_bindings WHERE trial_id=$1 AND arm=$2",
          [trialId, arm],
        )
      ).rows[0];
      if (old) {
        if (old.thread_id !== threadId)
          throw new DomainError("trial_arm_already_bound");
        return;
      }
      await c.query(
        "INSERT INTO experience_trial_arm_bindings(trial_id,arm,owner_id,thread_id) VALUES($1,$2,$3,$4)",
        [trialId, arm, owner, threadId],
      );
    });
  }
  async getTrialContext(ctx: ToolContext) {
    return toolTransaction(this.pool, ctx, async (c, t) => {
      const bound = (
        await c.query(
          "SELECT b.arm,x.*,x.start_session::text,x.target_session::text FROM experience_trial_arm_bindings b JOIN experience_trials x ON x.id=b.trial_id WHERE b.owner_id=$1 AND b.thread_id=$2",
          [ctx.ownerId, t.thread_id],
        )
      ).rows[0];
      if (!bound || t.role !== "prediction")
        throw new DomainError("trial_not_assigned", 403);
      const id =
        bound.payload.assignment[
          bound.arm === "control" ? "controlContextRef" : "candidateContextRef"
        ];
      const context = (
        await c.query(
          "SELECT payload FROM research_contexts WHERE id=$1 AND owner_id=$2",
          [id, ctx.ownerId],
        )
      ).rows[0];
      return {
        trialId: bound.id,
        arm: bound.arm,
        researchContextRef: id,
        context: context.payload,
        policy: {
          horizon: bound.horizon,
          startSession: bound.start_session,
          targetSession: bound.target_session,
          startOpenAt: bound.payload.startOpenAt,
          methodVersion: bound.payload.methodVersion,
          modelVersion: bound.payload.modelVersion,
        },
      };
    });
  }
  async publishTrialArm(ctx: ToolContext, input: unknown, key: string) {
    const request = forecastTrialArmInput.parse(input),
      hash = sha256(canonical(request));
    return awaitTaskVerification(() =>
      toolTransaction(this.pool, ctx, async (c, t) => {
        const bound = (
          await c.query(
            "SELECT x.* FROM experience_trial_arm_bindings b JOIN experience_trials x ON x.id=b.trial_id WHERE b.owner_id=$1 AND b.thread_id=$2 AND b.trial_id=$3 AND b.arm=$4",
            [ctx.ownerId, t.thread_id, request.trialId, request.arm],
          )
        ).rows[0];
        if (!bound || t.role !== "prediction")
          throw new DomainError("trial_not_assigned", 403);
        const old = (
          await c.query(
            "SELECT request_hash FROM experience_trial_arms WHERE trial_id=$1 AND arm=$2",
            [request.trialId, request.arm],
          )
        ).rows[0];
        if (old) {
          if (old.request_hash !== hash)
            throw new DomainError("trial_arm_immutable");
          return {
            trialId: request.trialId,
            arm: request.arm,
            status: "published",
          };
        }
        requireVerifiedTask(t);
        assertToolWrite(t);
        const now = this.clock();
        if (now >= new Date(bound.payload.startOpenAt))
          throw new DomainError("trial_expired", 422);
        await this.quant.run({
          operation: "validate_forecast",
          forecast: request.distribution,
        });
        const fingerprint = await executionFingerprint(c, t.id);
        if (
          fingerprint.modelVersion !== bound.payload.modelVersion ||
          fingerprint.methodVersion !== bound.payload.methodVersion
        )
          throw new DomainError("trial_method_changed", 422);
        const other = (
          await c.query(
            "SELECT payload FROM experience_trial_arms WHERE trial_id=$1 AND arm<>$2",
            [request.trialId, request.arm],
          )
        ).rows[0];
        if (
          other &&
          other.payload.runtimeIdentity !== fingerprint.runtimeIdentity
        )
          throw new DomainError("trial_runtime_changed", 422);
        if (this.clock() >= new Date(bound.payload.startOpenAt))
          throw new DomainError("trial_expired", 422);
        await c.query(
          "INSERT INTO experience_trial_arms(trial_id,arm,owner_id,task_id,operation_key,request_hash,payload,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8)",
          [
            request.trialId,
            request.arm,
            ctx.ownerId,
            t.id,
            key,
            hash,
            JSON.stringify({
              distribution: request.distribution,
              ...fingerprint,
            }),
            now,
          ],
        );
        return {
          trialId: request.trialId,
          arm: request.arm,
          status: "published",
        };
      }),
    );
  }
  async evaluateTrials(
    provider: MarketProvider | undefined,
    now = new Date(),
    limit = 20,
  ) {
    const evaluated: string[] = [];
    let pending = 0,
      failed = 0;
    await this.pool.query(
      `INSERT INTO experience_trial_checks(trial_id,checked_at,reason)
      SELECT t.id,$1,'expired_missing_arm' FROM experience_trials t WHERE (t.payload->>'startOpenAt')::timestamptz<=$1
      AND (SELECT count(*) FROM experience_trial_arms a WHERE a.trial_id=t.id)<2
      ON CONFLICT(trial_id) DO UPDATE SET checked_at=excluded.checked_at,reason=excluded.reason`,
      [now],
    );
    if (!provider) return { evaluated, pending, failed };
    const due = (
      await this.pool.query(
        `SELECT t.*,t.start_session::text,t.target_session::text,control.payload AS control_arm,candidate.payload AS candidate_arm FROM experience_trials t
      JOIN experience_trial_arms control ON control.trial_id=t.id AND control.arm='control'
      JOIN experience_trial_arms candidate ON candidate.trial_id=t.id AND candidate.arm='candidate'
      LEFT JOIN experience_trial_checks checks ON checks.trial_id=t.id
      LEFT JOIN experience_trial_evaluations e ON e.trial_id=t.id WHERE e.trial_id IS NULL AND t.evaluate_after<=$1 ORDER BY checks.checked_at NULLS FIRST,t.evaluate_after LIMIT $2`,
        [now, limit],
      )
    ).rows;
    for (const t of due) {
      try {
        const outcome = await settleSnapshot(
          provider,
          this.quant,
          t,
          {
            control: t.control_arm.distribution,
            candidate: t.candidate_arm.distribution,
          },
          now,
        );
        if (outcome.status !== "resolved") {
          pending++;
          await this.pool.query(
            "INSERT INTO experience_trial_checks(trial_id,checked_at,reason) VALUES($1,$2,$3) ON CONFLICT(trial_id) DO UPDATE SET checked_at=excluded.checked_at,reason=excluded.reason",
            [t.id, now, outcome.reason],
          );
          continue;
        }
        outcome.modelRuntimeIdentity = t.control_arm.runtimeIdentity;
        const saved = await this.pool.query(
          "INSERT INTO experience_trial_evaluations(trial_id,payload) VALUES($1,$2) ON CONFLICT DO NOTHING RETURNING trial_id",
          [t.id, JSON.stringify(outcome)],
        );
        if (saved.rowCount) evaluated.push(t.id);
      } catch {
        failed++;
        await this.pool.query(
          "INSERT INTO experience_trial_checks(trial_id,checked_at,reason) VALUES($1,$2,'market_unavailable') ON CONFLICT(trial_id) DO UPDATE SET checked_at=excluded.checked_at,reason=excluded.reason",
          [t.id, now],
        );
      }
    }
    return { evaluated, pending, failed };
  }
  async assessBatches(owner?: string) {
    const changes: Array<{
      lessonId: string;
      ownerId: string;
      status: string;
      batchId: string;
    }> = [];
    const lessons = (
      await this.pool.query(
        `SELECT l.id,l.owner_id FROM experience_lessons l WHERE l.status IN ('shadow','active') AND ($1::uuid IS NULL OR l.owner_id=$1) ORDER BY l.created_at LIMIT 200`,
        [owner ?? null],
      )
    ).rows;
    for (const ref of lessons) {
      const change = await transaction(this.pool, async (c) => {
        await lockOwner(c, ref.owner_id);
        const lesson = (
          await c.query(
            "SELECT * FROM experience_lessons WHERE id=$1 FOR UPDATE",
            [ref.id],
          )
        ).rows[0];
        const last = (
          await c.query(
            "SELECT created_at FROM experience_batches WHERE lesson_version_id=$1 ORDER BY created_at DESC LIMIT 1",
            [lesson.current_version_id],
          )
        ).rows[0];
        // Earliest trial per stock/event wins forever: revisions and repeats cannot inflate samples.
        const groups = (
          await c.query(
            `WITH firsts AS (
          SELECT DISTINCT ON(t.symbol,t.event_key) t.*,e.payload AS evaluation,e.created_at AS evaluated_at
          FROM experience_trials t JOIN experience_trial_evaluations e ON e.trial_id=t.id
          WHERE t.lesson_version_id=$1 ORDER BY t.symbol,t.event_key,t.published_at,t.id)
          SELECT * FROM firsts f WHERE NOT EXISTS(SELECT 1 FROM experience_batches b WHERE b.lesson_version_id=$1 AND f.id=ANY(b.trial_ids))
          AND ($2::timestamptz IS NULL OR f.published_at>$2) ORDER BY f.published_at,f.id`,
            [lesson.current_version_id, last?.created_at ?? null],
          )
        ).rows;
        // Keep model and existing active-policy version homogeneous within one batch.
        const partitions = new Map<string, any[]>();
        for (const g of groups) {
          const key = canonical({
            model: g.payload.modelVersion,
            runtime: g.evaluation.modelRuntimeIdentity,
            method: g.payload.methodVersion,
            active: g.payload.activeLessonVersionRefs,
          });
          const items = partitions.get(key) ?? [];
          items.push(g);
          partitions.set(key, items);
        }
        let sample: any[] | undefined;
        for (const partition of partitions.values()) {
          const companies = new Set<string>();
          for (let i = 0; i < partition.length; i++) {
            companies.add(partition[i].symbol);
            if (
              i >= 29 &&
              (lesson.scope === "company" || companies.size >= 5)
            ) {
              sample = partition.slice(0, i + 1);
              break;
            }
          }
          if (sample) break;
        }
        if (!sample) return null;
        const assessment = await this.quant.run({
          operation: "assess_experience_batch",
          samples: sample.map((s) => s.evaluation),
        });
        const status = assessment.passes ? "active" : "shadow",
          id = randomUUID();
        await c.query(
          "INSERT INTO experience_batches(id,owner_id,lesson_version_id,trial_ids,previous_status,resulting_status,payload) VALUES($1,$2,$3,$4,$5,$6,$7)",
          [
            id,
            lesson.owner_id,
            lesson.current_version_id,
            sample.map((s) => s.id),
            lesson.status,
            status,
            JSON.stringify({
              ...assessment,
              distinctCompanies: new Set(sample.map((s) => s.symbol)).size,
              engineeringThresholds: true,
            }),
          ],
        );
        await c.query("UPDATE experience_lessons SET status=$2 WHERE id=$1", [
          lesson.id,
          status,
        ]);
        return {
          lessonId: lesson.id,
          ownerId: lesson.owner_id,
          status,
          batchId: id,
        };
      });
      if (change) changes.push(change);
    }
    return changes;
  }
}
