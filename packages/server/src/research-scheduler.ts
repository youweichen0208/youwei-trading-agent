import { createHash, randomUUID } from "node:crypto";
import type { Pool } from "pg";
import { transaction, lockOwner } from "./db.js";
import { TaskStore } from "./tasks.js";
import { QuantRunner } from "./quant-runner.js";
import { NotificationStore } from "./notifications.js";
import type { MarketProvider } from "../../market/src/alpaca.js";

export const coveragePriority = {
  holding: 800,
  watchlist: 600,
  temporary: 400,
  candidate: 200,
  review: 900,
  trial: 100,
} as const;
type Tier = keyof typeof coveragePriority;
export type TradingSchedule = {
  session: string | null;
  marketOpen: string | null;
  marketClose: string | null;
  latestCompletedSession: string;
  temporaryExpirySession: string;
};
interface ResearchDependency {
  syncCoverage(owner: string): Promise<unknown>;
  listCoverage(owner: string): Promise<any[]>;
  collect(
    ctx: { ownerId: string },
    input: { symbol: string },
    key: string,
  ): Promise<any>;
  context(
    owner: string,
    symbol: string,
    asOf?: string,
    query?: string,
  ): Promise<any>;
}
interface SchedulerDependencies {
  research: ResearchDependency;
  scanner: { scan(owner: string, session?: string): Promise<any> };
  forecasts: {
    evaluateDue(
      provider: MarketProvider | undefined,
      now?: Date,
      limit?: number,
    ): Promise<any>;
  };
  experience?: {
    evaluateTrials(
      provider: MarketProvider | undefined,
      now?: Date,
      limit?: number,
    ): Promise<any>;
    assessBatches(owner?: string): Promise<any>;
    getTrialCandidates?(owner: string, limit?: number): Promise<any[]>;
    createTrialAssignment?(
      owner: string,
      candidate: { lessonVersionRef: string; symbol: string; horizon: 5 | 20 },
      input: { researchContextRef: string; eventKey: string },
    ): Promise<any>;
    bindTrialArm?(
      owner: string,
      trialId: string,
      arm: "control" | "candidate",
      threadId: string,
    ): Promise<unknown>;
  };
  market?: MarketProvider;
  calendar?: { run(input: Record<string, unknown>): Promise<any> };
}
const terminal = (status: string) =>
  ["completed", "failed", "cancelled", "interrupted"].includes(status);
const hash = (value: unknown) =>
  createHash("sha256").update(JSON.stringify(value)).digest("hex");

/** Deterministic scheduling/settlement lives outside Hermes and never consumes a Profile slot. */
export class ResearchScheduler {
  private notifications: NotificationStore;
  private calendar: NonNullable<SchedulerDependencies["calendar"]>;
  private lastPeriodicMinute = "";
  constructor(
    readonly pool: Pool,
    readonly dependencies: SchedulerDependencies,
  ) {
    this.notifications = new NotificationStore(pool);
    this.calendar = dependencies.calendar ?? new QuantRunner();
  }
  async tick(now = new Date(), signal?: AbortSignal) {
    const lock = await this.pool.connect();
    let acquired = false;
    try {
      acquired = (
        await lock.query(
          "SELECT pg_try_advisory_lock(hashtext('research_scheduler')) AS ok",
        )
      ).rows[0].ok;
      if (!acquired) return { skipped: true };
      if (signal?.aborted) return { skipped: false };
      const minute = now.toISOString().slice(0, 16);
      if (this.lastPeriodicMinute === minute) {
        await this.advanceJobs(now);
        return { skipped: false };
      }
      this.lastPeriodicMinute = minute;
      const schedule = (await this.calendar.run({
        operation: "research_schedule",
        asOf: now.toISOString(),
      })) as TradingSchedule;
      await this.runOnce(`settle:${minute}`, "settle", null, now, async () => {
        await this.dependencies.forecasts.evaluateDue(
          this.dependencies.market,
          now,
          50,
        );
        if (this.dependencies.experience) {
          await this.dependencies.experience.evaluateTrials(
            this.dependencies.market,
            now,
            50,
          );
          await this.dependencies.experience.assessBatches();
        }
      });
      await this.notifyRevisions();
      const owners = (
        await this.pool.query(
          `SELECT c.owner_id,coalesce(s.payload,'{}'::jsonb) AS settings FROM execution_contexts c
         LEFT JOIN research_settings s ON s.owner_id=c.owner_id WHERE c.kind='personal'`,
        )
      ).rows;
      for (const owner of owners) {
        if (signal?.aborted) break;
        if (owner.settings.enabled === false) continue;
        await this.dependencies.research.syncCoverage(owner.owner_id);
        await this.pool.query(
          "UPDATE research_coverage SET paused=true WHERE owner_id=$1 AND tier IN ('temporary','candidate') AND expires_session IS NOT NULL AND expires_session<=$2",
          [owner.owner_id, schedule.latestCompletedSession],
        );
        await this.enqueueReviews(
          owner.owner_id,
          schedule.latestCompletedSession,
        );
        await this.enqueueTrials(
          owner.owner_id,
          schedule.latestCompletedSession,
          owner.settings,
          now,
        );
        const open = schedule.marketOpen
          ? new Date(schedule.marketOpen).getTime()
          : null;
        if (
          schedule.session &&
          open &&
          now.getTime() >= open - 30 * 60_000 &&
          now.getTime() < open
        )
          await this.runOnce(
            `preopen:${owner.owner_id}:${schedule.session}`,
            "preopen",
            owner.owner_id,
            now,
            () =>
              this.checkCoverage(
                owner.owner_id,
                schedule.session!,
                owner.settings,
                now,
                true,
                schedule.latestCompletedSession,
              ),
          );
        await this.runOnce(
          `close:${owner.owner_id}:${schedule.latestCompletedSession}`,
          "close",
          owner.owner_id,
          now,
          async () => {
            await this.checkCoverage(
              owner.owner_id,
              schedule.latestCompletedSession,
              owner.settings,
              now,
              false,
            );
            const scan = await this.dependencies.scanner.scan(
              owner.owner_id,
              schedule.latestCompletedSession,
            );
            const candidateLimit = Math.min(
              30,
              Math.max(0, Number(owner.settings.dailyCandidateLimit ?? 10)),
            );
            let added = 0;
            for (const candidate of scan.selected ?? []) {
              if (added >= candidateLimit || signal?.aborted) break;
              const prior = (
                await this.pool.query(
                  "SELECT 1 FROM research_coverage WHERE owner_id=$1 AND symbol=$2 AND NOT paused AND (expires_session IS NULL OR expires_session>$3) LIMIT 1",
                  [
                    owner.owner_id,
                    candidate.symbol,
                    schedule.latestCompletedSession,
                  ],
                )
              ).rowCount;
              if (prior) continue;
              const context = await this.dependencies.research.context(
                owner.owner_id,
                candidate.symbol,
              );
              const job = await this.enqueueJob(
                owner.owner_id,
                candidate.symbol,
                "candidate",
                schedule.latestCompletedSession,
                `candidate:${schedule.latestCompletedSession}:${candidate.symbol}`,
                {
                  researchContextRef: context.contextRef,
                  selection: candidate,
                },
                owner.settings,
              );
              if (job) {
                added++;
                await this.pool.query(
                  `INSERT INTO research_coverage(owner_id,symbol,tier,priority,expires_session) VALUES($1,$2,'candidate',20,$3)
                ON CONFLICT(owner_id,symbol) DO UPDATE SET paused=false,expires_session=$3,updated_at=now()
                WHERE research_coverage.tier='candidate'`,
                  [
                    owner.owner_id,
                    candidate.symbol,
                    schedule.temporaryExpirySession,
                  ],
                );
              }
            }
          },
        );
        await this.promoteDeferred(
          owner.owner_id,
          schedule.latestCompletedSession,
          owner.settings,
          now,
        );
        const hour = Number(
          new Intl.DateTimeFormat("en-GB", {
            timeZone: "Asia/Shanghai",
            hour: "2-digit",
            hourCycle: "h23",
          }).format(now),
        );
        if (hour >= Number(owner.settings.digestHour ?? 8))
          await this.runOnce(
            `digest:${owner.owner_id}:${schedule.latestCompletedSession}`,
            "digest",
            owner.owner_id,
            now,
            () =>
              this.digest(owner.owner_id, schedule.latestCompletedSession, now),
          );
      }
      await this.advanceJobs(now);
      return { skipped: false };
    } finally {
      if (acquired)
        await lock.query(
          "SELECT pg_advisory_unlock(hashtext('research_scheduler'))",
        );
      lock.release();
    }
  }
  private async runOnce(
    key: string,
    kind: string,
    owner: string | null,
    now: Date,
    run: () => Promise<unknown>,
  ) {
    const claimed = (
      await this.pool.query(
        `INSERT INTO research_schedule_runs(key,kind,owner_id,status,lease_until) VALUES($1,$2,$3,'running',$4::timestamptz+interval '5 minutes')
       ON CONFLICT(key) DO UPDATE SET status='running',lease_until=excluded.lease_until,attempts=research_schedule_runs.attempts+1
       WHERE research_schedule_runs.status<>'completed' AND research_schedule_runs.lease_until<=$4 RETURNING key`,
        [key, kind, owner, now],
      )
    ).rowCount;
    if (!claimed) return;
    try {
      await run();
      await this.pool.query(
        "UPDATE research_schedule_runs SET status='completed',completed_at=$2,error_code=NULL WHERE key=$1",
        [key, now],
      );
    } catch {
      await this.pool.query(
        "UPDATE research_schedule_runs SET status='failed',error_code='dependency_unavailable',lease_until=$2::timestamptz+interval '1 minute' WHERE key=$1",
        [key, now],
      );
    }
  }
  async enqueueJob(
    owner: string,
    symbol: string,
    tier: Tier,
    session: string,
    key: string,
    checkpoint: Record<string, unknown> = {},
    settings: Record<string, unknown> = {},
  ) {
    return transaction(this.pool, async (c) => {
      await lockOwner(c, owner);
      const old = (
        await c.query(
          "SELECT * FROM research_jobs WHERE owner_id=$1 AND job_key=$2",
          [owner, key],
        )
      ).rows[0];
      if (old) return old;
      if (tier === "candidate" && checkpoint.selection)
        checkpoint = { ...checkpoint, selectionSession: session };
      // Collapse not-yet-started refreshes of the same stock into its durable backlog.
      // Its age is retained for fair ordering while the frozen evidence is refreshed.
      if (!["review", "trial"].includes(tier)) {
        const waiting = (
          await c.query(
            "SELECT * FROM research_jobs WHERE owner_id=$1 AND symbol=$2 AND tier NOT IN ('review','trial') AND stage='collect' AND active_task_id IS NULL AND status IN ('pending','deferred') ORDER BY created_at LIMIT 1 FOR UPDATE",
            [owner, symbol],
          )
        ).rows[0];
        if (waiting)
          return (
            await c.query(
              "UPDATE research_jobs SET tier=$2,priority=$3,checkpoint=$4,updated_at=now() WHERE id=$1 RETURNING *",
              [
                waiting.id,
                tier,
                coveragePriority[tier],
                JSON.stringify({ ...waiting.checkpoint, ...checkpoint }),
              ],
            )
          ).rows[0];
      }
      let status = "pending";
      if (tier !== "review") {
        const count = (
          await c.query(
            "SELECT count(DISTINCT symbol)::int AS n FROM research_jobs WHERE owner_id=$1 AND session=$2 AND tier<>'review' AND status<>'deferred' AND error_code IS DISTINCT FROM 'coverage_expired' AND error_code IS DISTINCT FROM 'trial_expired'",
            [owner, session],
          )
        ).rows[0].n;
        const limit = Math.min(
          100,
          Math.max(1, Number(settings.dailyResearchLimit ?? 30)),
        );
        const sameStock = (
          await c.query(
            "SELECT 1 FROM research_jobs WHERE owner_id=$1 AND session=$2 AND symbol=$3 AND tier<>'review' AND status<>'deferred' AND error_code IS DISTINCT FROM 'coverage_expired' AND error_code IS DISTINCT FROM 'trial_expired'",
            [owner, session, symbol],
          )
        ).rowCount;
        if (count >= limit && !sameStock) status = "deferred";
        if (tier === "candidate" && checkpoint.selection) {
          const candidates = (
            await c.query(
              "SELECT count(*)::int AS n FROM research_jobs WHERE owner_id=$1 AND coalesce(checkpoint->>'selectionSession',session::text)=$2 AND tier='candidate' AND checkpoint ? 'selection'",
              [owner, session],
            )
          ).rows[0].n;
          if (
            candidates >=
            Math.min(
              30,
              Math.max(0, Number(settings.dailyCandidateLimit ?? 10)),
            )
          )
            return null;
        }
      }
      return (
        await c.query(
          `INSERT INTO research_jobs(id,owner_id,job_key,symbol,tier,stage,priority,session,checkpoint,status)
         VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING *`,
          [
            randomUUID(),
            owner,
            key,
            symbol,
            tier,
            tier === "review"
              ? "review"
              : tier === "trial"
                ? "trial"
                : "collect",
            coveragePriority[tier],
            session,
            JSON.stringify(checkpoint),
            status,
          ],
        )
      ).rows[0];
    });
  }
  async promoteDeferred(
    owner: string,
    session: string,
    settings: Record<string, unknown> = {},
    now = new Date(),
  ) {
    return transaction(this.pool, async (c) => {
      await lockOwner(c, owner);
      // A stock waiting overnight must use the next day's allowance when it starts.
      await c.query(
        "UPDATE research_jobs SET status='deferred' WHERE owner_id=$1 AND session<$2 AND status='pending' AND active_task_id IS NULL AND tier<>'review'",
        [owner, session],
      );
      await c.query(
        `UPDATE research_jobs j SET status='failed',error_code='coverage_expired',updated_at=$3 WHERE j.owner_id=$1
        AND j.tier<>'candidate' AND j.status IN ('deferred','pending') AND j.stage='collect' AND j.active_task_id IS NULL
        AND EXISTS(SELECT 1 FROM research_coverage v WHERE v.owner_id=j.owner_id AND v.symbol=j.symbol AND (v.paused OR v.expires_session<=$2))`,
        [owner, session, now],
      );
      await c.query(
        "UPDATE research_jobs SET status='failed',error_code='trial_expired',updated_at=$2 WHERE owner_id=$1 AND status='deferred' AND tier='trial' AND (checkpoint->>'trialStartOpenAt')::timestamptz<=$2",
        [owner, now],
      );
      const admitted = new Set<string>(
        (
          await c.query(
            "SELECT DISTINCT symbol FROM research_jobs WHERE owner_id=$1 AND session=$2 AND tier<>'review' AND status<>'deferred' AND error_code IS DISTINCT FROM 'coverage_expired' AND error_code IS DISTINCT FROM 'trial_expired'",
            [owner, session],
          )
        ).rows.map((row: any) => row.symbol),
      );
      const limit = Math.min(
        100,
        Math.max(1, Number(settings.dailyResearchLimit ?? 30)),
      );
      const waiting = (
        await c.query(
          `SELECT j.*,v.paused FROM research_jobs j LEFT JOIN research_coverage v ON v.owner_id=j.owner_id AND v.symbol=j.symbol
        WHERE j.owner_id=$1 AND j.status='deferred' ORDER BY j.priority DESC,v.last_analyzed_at ASC NULLS FIRST,j.created_at,j.id FOR UPDATE OF j`,
          [owner],
        )
      ).rows;
      for (const job of waiting) {
        if (job.tier === "candidate" && (settings.dailyCandidateLimit === 0 || job.paused)) continue;
        if (admitted.size >= limit && !admitted.has(job.symbol)) continue;
        await c.query(
          "UPDATE research_jobs SET status='pending',session=$2,updated_at=$3 WHERE id=$1",
          [job.id, session, now],
        );
        admitted.add(job.symbol);
      }
    });
  }
  private async checkCoverage(
    owner: string,
    session: string,
    settings: Record<string, unknown>,
    now: Date,
    coreOnly: boolean,
    priceSession = session,
  ) {
    const coverage = (
      await this.dependencies.research.listCoverage(owner)
    ).filter(
      (row: any) =>
        !row.paused &&
        (!row.expires_session || row.expires_session > session) &&
        (!coreOnly || row.tier === "holding" || row.tier === "watchlist"),
    );
    coverage.sort(
      (a: any, b: any) =>
        coveragePriority[b.tier as Tier] - coveragePriority[a.tier as Tier] ||
        new Date(a.last_analyzed_at ?? 0).getTime() -
          new Date(b.last_analyzed_at ?? 0).getTime(),
    );
    for (const row of coverage) {
      const key = `check:${session}:${coreOnly ? "preopen" : "close"}:${row.symbol}`;
      const collected = await this.dependencies.research.collect(
        { ownerId: owner },
        { symbol: row.symbol },
        key,
      );
      let price: unknown = null,
        priceFailure: string | null = null;
      if (this.dependencies.market) {
        try {
          price = await this.dependencies.market.bars(
            {
              symbol: row.symbol,
              start: priceSession,
              end: priceSession,
              adjustment: "split",
            },
            AbortSignal.timeout(30_000),
          );
        } catch {
          priceFailure = "market_unavailable";
        }
      }
      if (collected.status === "running") throw new Error("collection_pending");
      const active = (
        await this.pool.query(
          `SELECT horizon FROM forecasts f WHERE owner_id=$1 AND symbol=$2 AND target_close_at>$3
        AND NOT EXISTS(SELECT 1 FROM forecasts newer WHERE newer.supersedes_id=f.id)`,
          [owner, row.symbol, now],
        )
      ).rows;
      const missingHorizons = [5, 20].filter(
        (h) => !active.some((f: any) => f.horizon === h),
      );
      const fingerprint = hash({
        evidence: collected.fingerprint ?? collected.evidenceRefs ?? [],
        price,
        missingHorizons,
      });
      const old = (
        await this.pool.query(
          "SELECT fingerprint FROM research_checks WHERE owner_id=$1 AND symbol=$2",
          [owner, row.symbol],
        )
      ).rows[0];
      const recordCheck = () =>
        this.pool.query(
          `INSERT INTO research_checks(owner_id,symbol,fingerprint,checked_at,payload) VALUES($1,$2,$3,$4,$5)
         ON CONFLICT(owner_id,symbol) DO UPDATE SET fingerprint=excluded.fingerprint,checked_at=excluded.checked_at,payload=excluded.payload`,
          [
            owner,
            row.symbol,
            fingerprint,
            now,
            JSON.stringify({
              collectionRef: collected.collectionRef,
              status: collected.status,
              failures: collected.failures ?? [],
              priceFailure,
            }),
          ],
        );
      if (old?.fingerprint === fingerprint && !missingHorizons.length) {
        await recordCheck();
        continue;
      }
      const context = await this.dependencies.research.context(
        owner,
        row.symbol,
      );
      const queued = await this.enqueueJob(
        owner,
        row.symbol,
        row.tier,
        session,
        `change:${row.symbol}:${session}:${fingerprint}`,
        {
          researchContextRef: context.contextRef,
          collectionRef: collected.collectionRef,
          evidenceRefs: collected.evidenceRefs ?? [],
          changeFingerprint: fingerprint,
          missingHorizons,
        },
        settings,
      );
      // The durable job (including deferred work) precedes this watermark. A crash
      // after enqueue retries the same idempotency key instead of losing the event.
      if (queued) await recordCheck();
    }
  }
  private async enqueueReviews(owner: string, session: string) {
    const rows = (
      await this.pool.query(
        `SELECT f.id,f.symbol,e.result_id FROM forecasts f JOIN forecast_evaluations e ON e.forecast_id=f.id
       WHERE f.owner_id=$1 AND NOT EXISTS(SELECT 1 FROM forecast_reviews r WHERE r.forecast_id=f.id)
       ORDER BY e.created_at LIMIT 30`,
        [owner],
      )
    ).rows;
    for (const row of rows)
      await this.enqueueJob(
        owner,
        row.symbol,
        "review",
        session,
        `review:${row.id}`,
        { forecastId: row.id, evaluationRef: row.result_id },
      );
  }
  private async notifyRevisions() {
    const changes = (
      await this.pool
        .query(`SELECT f.id,f.owner_id,f.symbol,f.horizon,f.payload,p.payload AS prior
      FROM forecasts f JOIN forecasts p ON p.id=f.supersedes_id AND p.owner_id=f.owner_id
      WHERE NOT EXISTS(SELECT 1 FROM notifications n WHERE n.owner_id=f.owner_id AND n.event_key='forecast-change:'||f.id::text)
      ORDER BY f.published_at LIMIT 100`)
    ).rows;
    for (const row of changes) {
      const pct = (value: string) => `${(Number(value) * 100).toFixed(1)}%`;
      await this.notifications.enqueue(
        row.owner_id,
        "forecast_changed",
        `forecast-change:${row.id}`,
        {
          title: `${row.symbol} · ${row.horizon} 交易日判断更新`,
          text: `预期收益：${pct(row.prior.meanReturn)} → ${pct(row.payload.meanReturn)}\n上涨概率：${pct(row.prior.upProbability)} → ${pct(row.payload.upProbability)}\n\n原判断：${row.prior.thesis}\n\n新判断与依据：${row.payload.thesis}\n\n原预测仍保留并按原期限验证。`,
          href: `/lab?forecast=${row.id}`,
          forecastId: row.id,
        },
      );
    }
  }
  private async enqueueTrials(
    owner: string,
    session: string,
    settings: Record<string, unknown>,
    now: Date,
  ) {
    const experience = this.dependencies.experience;
    if (!experience?.createTrialAssignment || !experience.bindTrialArm) return;
    const incomplete = (
      await this.pool.query(
        `SELECT x.* FROM experience_trials x WHERE x.owner_id=$1
      AND (x.payload->>'startOpenAt')::timestamptz>$2
      AND (SELECT count(*) FROM experience_trial_arm_bindings b WHERE b.trial_id=x.id)<2
      ORDER BY x.published_at LIMIT 1`,
        [owner, now],
      )
    ).rows[0];
    if (incomplete) {
      await this.ensureTrialJobs(
        owner,
        session,
        settings,
        incomplete.symbol,
        incomplete.horizon,
        incomplete.event_key,
        { trialId: incomplete.id, ...incomplete.payload.assignment },
      );
      return;
    }
    const today = (
      await this.pool.query(
        "SELECT count(DISTINCT checkpoint->>'trialId')::int AS n FROM research_jobs WHERE owner_id=$1 AND session=$2 AND tier='trial'",
        [owner, session],
      )
    ).rows[0].n;
    if (today >= 3) return;
    // The analyst must explicitly identify an applicable lesson and a saved event.
    // No date-based or arbitrary-article trial manufacture is allowed.
    const proposals = (
      await this.pool.query(
        `SELECT r.payload,r.lineage FROM results r
      WHERE r.owner_id=$1 AND r.kind='research_summary' AND r.payload ? 'trialCandidates'
        AND r.created_at >= $2::timestamptz-interval '7 days' ORDER BY r.created_at DESC LIMIT 50`,
        [owner, now],
      )
    ).rows;
    for (const proposal of proposals) {
      if (
        !proposal.lineage.researchContextRef ||
        !Array.isArray(proposal.payload.trialCandidates)
      )
        continue;
      const context = (
        await this.pool.query(
          "SELECT id,symbol,payload FROM research_contexts WHERE owner_id=$1 AND id=$2",
          [owner, proposal.lineage.researchContextRef],
        )
      ).rows[0];
      if (!context) continue;
      for (const item of proposal.payload.trialCandidates.slice(0, 3)) {
        const lesson = (
          await this.pool.query(
            `SELECT v.* FROM experience_versions v JOIN experience_lessons l ON l.current_version_id=v.id
          WHERE v.id=$1 AND v.owner_id=$2 AND l.status IN ('shadow','active')`,
            [item.lessonVersionRef, owner],
          )
        ).rows[0];
        const event = (context.payload.evidence ?? []).find(
          (e: any) => e.eventKey === item.eventKey,
        );
        if (
          !lesson ||
          !event?.published_at ||
          new Date(event.published_at) <= new Date(lesson.created_at)
        )
          continue;
        const existing = (
          await this.pool.query(
            `SELECT 1 FROM experience_trials WHERE owner_id=$1 AND lesson_version_id=$2 AND symbol=$3 AND event_key=$4`,
            [owner, lesson.id, context.symbol, item.eventKey],
          )
        ).rowCount;
        if (existing) continue;
        const assignment = await experience.createTrialAssignment(
          owner,
          {
            lessonVersionRef: lesson.id,
            symbol: context.symbol,
            horizon: lesson.horizon,
          },
          { researchContextRef: context.id, eventKey: item.eventKey },
        );
        await this.ensureTrialJobs(
          owner,
          session,
          settings,
          context.symbol,
          lesson.horizon,
          item.eventKey,
          assignment,
        );
        return;
      }
    }
  }
  private async ensureTrialJobs(
    owner: string,
    session: string,
    settings: Record<string, unknown>,
    symbol: string,
    horizon: number,
    eventKey: string,
    assignment: any,
  ) {
    const experience = this.dependencies.experience!;
    for (const arm of ["control", "candidate"] as const) {
      const job = await this.enqueueJob(
        owner,
        symbol,
        "trial",
        session,
        `trial:${assignment.trialId}:${arm}`,
        {
          trialId: assignment.trialId,
          trialArm: arm,
          researchContextRef:
            arm === "control"
              ? assignment.controlContextRef
              : assignment.candidateContextRef,
          horizon,
          trialStartOpenAt: assignment.startOpenAt,
          eventKey,
        },
        settings,
      );
      if (!job) return;
      // Job admission is durable; recreate only missing threads after interruption.
      if (!job.thread_id) {
        await transaction(this.pool, async (c) => {
          await lockOwner(c, owner);
          const current = (
            await c.query(
              "SELECT thread_id FROM research_jobs WHERE id=$1 FOR UPDATE",
              [job.id],
            )
          ).rows[0];
          if (current.thread_id) {
            job.thread_id = current.thread_id;
            return;
          }
          const thread = await new TaskStore(this.pool, c).createThread(owner);
          await c.query(
            "UPDATE threads SET source='background',title=$2 WHERE id=$1",
            [thread.id, `${symbol} 影子试验 ${arm}`],
          );
          await c.query("UPDATE research_jobs SET thread_id=$2 WHERE id=$1", [
            job.id,
            thread.id,
          ]);
          job.thread_id = thread.id;
        });
      }
      await experience.bindTrialArm!(
        owner,
        assignment.trialId,
        arm,
        job.thread_id,
      );
    }
  }
  /** A phase ends only after its real task and children end; the budget is not a preemption guarantee. */
  async advanceJobs(now = new Date()) {
    const rows = (
      await this.pool.query(
        "SELECT * FROM research_jobs WHERE status IN ('pending','running','blocked') ORDER BY priority DESC,created_at LIMIT 100",
      )
    ).rows;
    for (const job of rows) {
      await transaction(this.pool, async (c) => {
        await lockOwner(c, job.owner_id);
        const current = (
          await c.query("SELECT * FROM research_jobs WHERE id=$1 FOR UPDATE", [
            job.id,
          ])
        ).rows[0];
        if (
          !current ||
          !["pending", "running", "blocked"].includes(current.status)
        )
          return;
        if (current.active_task_id) {
          const task = (
            await c.query("SELECT * FROM tasks WHERE id=$1", [
              current.active_task_id,
            ])
          ).rows[0];
          if (!terminal(task.status)) {
            if (task.status === "unknown")
              await c.query(
                "UPDATE research_jobs SET status='blocked',error_code='task_unknown',updated_at=$2 WHERE id=$1",
                [job.id, now],
              );
            return;
          }
          if (task.status !== "completed") {
            await c.query(
              "UPDATE research_jobs SET status='failed',error_code=$2,updated_at=$3 WHERE id=$1",
              [
                job.id,
                task.cancel_reason ?? task.failure_code ?? task.status,
                now,
              ],
            );
            return;
          }
          const savedResults = (
            await c.query(
              "SELECT r.id FROM results r JOIN tasks t ON t.id=r.task_id WHERE t.root_id=$1",
              [task.id],
            )
          ).rows.map((r: any) => r.id);
          const savedDatasets = (
            await c.query(
              "SELECT d.id FROM datasets d JOIN tool_operations op ON op.id=d.operation_id JOIN tasks t ON t.id=op.task_id WHERE t.root_id=$1",
              [task.id],
            )
          ).rows.map((r: any) => r.id);
          const checkpoints: Record<string, any> = {
            ...current.checkpoint,
            resultRefs: [
              ...new Set([
                ...(current.checkpoint.resultRefs ?? []),
                ...savedResults,
              ]),
            ],
            datasetRefs: [
              ...new Set([
                ...(current.checkpoint.datasetRefs ?? []),
                ...savedDatasets,
              ]),
            ],
          };
          const summaryRow = (
            await c.query(
              "SELECT payload,lineage FROM results WHERE task_id=$1 AND kind='research_summary' ORDER BY created_at DESC LIMIT 1",
              [task.id],
            )
          ).rows[0];
          const summary = summaryRow?.payload;
          const frozen = (
            await c.query(
              "SELECT response FROM tool_calls WHERE task_id=$1 AND tool='read_research_context' AND response ? 'contextRef' ORDER BY created_at DESC LIMIT 1",
              [task.id],
            )
          ).rows[0]?.response;
          checkpoints.researchContextRef =
            summaryRow?.lineage?.researchContextRef ??
            frozen?.contextRef ??
            checkpoints.researchContextRef;
          checkpoints.historicalDatasetRefs = [
            ...new Set([
              ...(current.checkpoint.historicalDatasetRefs ?? []),
              ...checkpoints.datasetRefs,
            ]),
          ];
          checkpoints.datasetRefs = checkpoints.researchContextRef
            ? (
                await c.query(
                  `SELECT d.id FROM research_contexts x JOIN datasets d ON (x.payload->'datasetRefs') ? d.id::text
              WHERE x.id=$1 AND x.owner_id=$2 AND d.owner_id=$2
                AND d.metadata->>'symbol'=$3 AND d.metadata->>'adjustment'='split' ORDER BY d.id`,
                  [checkpoints.researchContextRef, job.owner_id, job.symbol],
                )
              ).rows.map((d: any) => d.id)
            : [];
          if (Array.isArray(summary?.forecastIds))
            checkpoints.forecastIds = summary.forecastIds;
          if (summary?.forecastAction)
            checkpoints.forecastAction = summary.forecastAction;
          if (
            current.stage === "analyze" &&
            summary?.forecastAction === "retain" &&
            Array.isArray(summary.forecastIds)
          ) {
            const retained = (
              await c.query(
                "SELECT id,horizon FROM forecasts f WHERE owner_id=$1 AND symbol=$2 AND id=ANY($3::uuid[]) AND target_close_at>$4 AND NOT EXISTS(SELECT 1 FROM forecasts newer WHERE newer.supersedes_id=f.id)",
                [job.owner_id, job.symbol, summary.forecastIds, now],
              )
            ).rows;
            if (
              [5, 20].every((h) => retained.some((f: any) => f.horizon === h))
            ) {
              await c.query(
                "UPDATE research_jobs SET status='completed',checkpoint=$2,error_code=NULL,updated_at=$3 WHERE id=$1",
                [
                  job.id,
                  JSON.stringify({ ...checkpoints, forecastAction: "retain" }),
                  now,
                ],
              );
              await c.query(
                "UPDATE research_coverage SET last_analyzed_at=$3 WHERE owner_id=$1 AND symbol=$2",
                [job.owner_id, job.symbol, now],
              );
              return;
            }
          }
          const next =
            current.stage === "collect"
              ? "analyze"
              : current.stage === "analyze"
                ? "publish"
                : null;
          if (!next) {
            if (current.stage === "publish") {
              const forecasts = (
                await c.query(
                  "SELECT DISTINCT f.horizon FROM forecasts f JOIN tasks t ON t.id=f.task_id WHERE f.owner_id=$2 AND f.symbol=$3 AND (t.root_id=$1 OR (f.id=ANY($4::uuid[]) AND f.target_close_at>$5)) AND NOT EXISTS(SELECT 1 FROM forecasts newer WHERE newer.supersedes_id=f.id)",
                  [
                    task.id,
                    job.owner_id,
                    job.symbol,
                    checkpoints.forecastIds ?? [],
                    now,
                  ],
                )
              ).rows;
              if (
                ![5, 20].every((h) =>
                  forecasts.some((f: any) => f.horizon === h),
                )
              ) {
                await c.query(
                  "UPDATE research_jobs SET status='failed',error_code='forecast_output_missing',updated_at=$2 WHERE id=$1",
                  [job.id, now],
                );
                return;
              }
            }
            if (
              current.stage === "review" &&
              !(
                await c.query(
                  "SELECT 1 FROM forecast_reviews WHERE task_id=$1 AND forecast_id=$2",
                  [task.id, current.checkpoint.forecastId],
                )
              ).rowCount
            ) {
              await c.query(
                "UPDATE research_jobs SET status='failed',error_code='review_output_missing',updated_at=$2 WHERE id=$1",
                [job.id, now],
              );
              return;
            }
            if (
              current.stage === "trial" &&
              !(
                await c.query(
                  "SELECT 1 FROM experience_trial_arms a JOIN tasks t ON t.id=a.task_id WHERE t.root_id=$1 AND a.trial_id=$2 AND a.arm=$3",
                  [
                    task.id,
                    current.checkpoint.trialId,
                    current.checkpoint.trialArm,
                  ],
                )
              ).rowCount
            ) {
              await c.query(
                "UPDATE research_jobs SET status='failed',error_code='trial_output_missing',updated_at=$2 WHERE id=$1",
                [job.id, now],
              );
              return;
            }
            await c.query(
              "UPDATE research_jobs SET status='completed',checkpoint=$2,updated_at=$3,error_code=NULL WHERE id=$1",
              [job.id, JSON.stringify(checkpoints), now],
            );
            if (!["review", "trial"].includes(current.tier))
              await c.query(
                "UPDATE research_coverage SET last_analyzed_at=$3 WHERE owner_id=$1 AND symbol=$2",
                [job.owner_id, job.symbol, now],
              );
            return;
          }
          await c.query(
            "UPDATE research_jobs SET stage=$2,status='pending',active_task_id=NULL,checkpoint=$3,error_code=NULL,updated_at=$4 WHERE id=$1",
            [job.id, next, JSON.stringify(checkpoints), now],
          );
          current.stage = next;
          current.checkpoint = checkpoints;
          current.active_task_id = null;
        }
        const busy = (
          await c.query(
            "SELECT 1 FROM tasks WHERE owner_id=$1 AND parent_id IS NULL AND status IN ('queued','running','stopping','unknown') LIMIT 1",
            [job.owner_id],
          )
        ).rowCount;
        if (busy) return;
        const setting = (
          await c.query(
            "SELECT payload FROM research_settings WHERE owner_id=$1",
            [job.owner_id],
          )
        ).rows[0]?.payload;
        if (setting?.enabled === false) return;
        if (
          current.stage === "trial" &&
          new Date(current.checkpoint.trialStartOpenAt) <= now
        ) {
          await c.query(
            "UPDATE research_jobs SET status='failed',error_code='trial_expired',updated_at=$2 WHERE id=$1",
            [job.id, now],
          );
          return;
        }
        if (current.tier === "candidate" && (
          setting?.dailyCandidateLimit === 0 || (await c.query(
            "SELECT 1 FROM research_coverage WHERE owner_id=$1 AND symbol=$2 AND paused",
            [job.owner_id, job.symbol],
          )).rowCount
        )) return;
        const schedule = await this.calendar.run({ operation: "research_schedule", asOf: now.toISOString() });
        current.checkpoint.marketEndSession = schedule.latestCompletedSession;
        await c.query("UPDATE research_jobs SET checkpoint=$2 WHERE id=$1", [job.id, JSON.stringify(current.checkpoint)]);
        const tasks = new TaskStore(this.pool, c);
        const task = await tasks.submitSystem(
          job.owner_id,
          `job:${job.id}:${current.stage}`,
          {
            input: `完成 ${job.symbol} 的 ${current.stage} 研究阶段。按阶段保存证据和结果；publish 阶段一次发布 5、20 个交易日预测。`,
            researchJobId: job.id,
            researchPhase: current.stage,
            symbol: job.symbol,
            coverageType: job.tier,
            ...current.checkpoint,
          },
          {
            threadId: current.thread_id ?? undefined,
            priority: job.priority,
            researchJobId: job.id,
            budgetSeconds: current.stage === "publish" ? 600 : 300,
          },
        );
        await c.query(
          "UPDATE research_jobs SET status='running',active_task_id=$2,thread_id=$3,updated_at=$4 WHERE id=$1",
          [job.id, task.id, task.thread_id, now],
        );
      });
    }
  }
  private async digest(owner: string, session: string, now: Date) {
    const rows = (await this.pool.query(
      `SELECT DISTINCT ON (symbol) symbol,tier,status,stage,error_code FROM research_jobs
       WHERE owner_id=$1 AND session=$2 ORDER BY symbol,updated_at DESC,created_at DESC,id DESC`,
      [owner, session],
    )).rows;
    const backlog = (await this.pool.query(
      "SELECT count(*)::int AS n FROM research_jobs WHERE owner_id=$1 AND status IN ('pending','deferred','blocked') AND session<$2",
      [owner, session],
    )).rows[0].n;
    const counts = (await this.pool.query(
      `SELECT count(DISTINCT f.id)::int AS added,
       count(DISTINCT f.id) FILTER (WHERE f.supersedes_id IS NOT NULL)::int AS changes
       FROM forecasts f JOIN tasks t ON t.id=f.task_id
       JOIN tasks root ON root.id=t.root_id
       JOIN research_jobs j ON j.id=root.research_job_id
       WHERE f.owner_id=$1 AND j.session=$2`, [owner, session],
    )).rows[0];
    const reasons: Record<string,string> = {
      deadline_exceeded: "阶段超时", budget_exceeded: "阶段超时",
      forecast_output_missing: "未保存完整的 5/20 交易日预测",
      market_session_not_finalized: "所请求日线尚未完成",
      task_unknown: "执行状态待核实", coverage_expired: "覆盖已到期",
    };
    let failures = 0;
    const detail = rows.filter((row: any) => row.status !== "failed" || ++failures <= 5)
      .map((row: any) => `${row.symbol} · ${row.status === "completed" ? "研究完成" : row.status === "failed" ? `未完成（${reasons[row.error_code] ?? "阶段执行失败，请查看任务详情"}）` : "待完成"}`).join("\n");
    const text = `研究股票：${rows.length} · 完成：${rows.filter((r: any) => r.status === "completed").length} · 新增预测：${counts.added} · 失败：${rows.filter((r: any) => r.status === "failed").length} · 历史积压：${backlog}\n判断变化：${counts.changes} 条\n\n${detail || "当日没有研究任务。"}${failures > 5 ? "\n更多失败详情请查看工作台。" : ""}`;
    await this.notifications.enqueue(
      owner,
      "daily_digest",
      `digest:${session}`,
      {
        title: `${session} 研究日报`,
        text,
      },
    );
  }
}
