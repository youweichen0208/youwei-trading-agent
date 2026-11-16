import { randomUUID } from "node:crypto";
import type { Pool } from "pg";
import { z } from "zod";
import { symbol } from "./business.js";
import { transaction } from "./db.js";
import { DomainError } from "./tasks.js";

/** Account-level notebook. Never contains observed facts, only user-authored reflection. */
export const MAX_AGENT_NOTES = 20;
export const MAX_NOTE_BODY_CHARS = 2000;
export const MAX_NOTE_PROMPT_CHARS = 9000;

const tag = z.string().trim().min(1).max(30);
const tags = z
  .array(tag)
  .max(10)
  .transform((v) => [...new Set(v)]);

export const personalNoteInput = z
  .object({
    title: z.string().trim().min(1).max(200),
    body: z.string().trim().min(1).max(20000),
    tags: tags.default([]),
    symbol: symbol.optional(),
    pinned: z.boolean().default(false),
    useInAgent: z.boolean().default(true),
  })
  .strict();

export const personalNoteUpdateInput = z
  .object({
    title: z.string().trim().min(1).max(200).optional(),
    body: z.string().trim().min(1).max(20000).optional(),
    tags: tags.optional(),
    symbol: symbol.nullable().optional(),
    pinned: z.boolean().optional(),
    useInAgent: z.boolean().optional(),
  })
  .strict()
  .refine((v) => Object.keys(v).length > 0, "empty_update");

export type PersonalNote = {
  id: string;
  title: string;
  body: string;
  tags: string[];
  symbol: string | null;
  pinned: boolean;
  useInAgent: boolean;
  createdAt: Date;
  updatedAt: Date;
};

const view = (row: any): PersonalNote => ({
  id: row.id,
  title: row.title,
  body: row.body,
  tags: row.tags ?? [],
  symbol: row.symbol,
  pinned: row.pinned,
  useInAgent: row.use_in_agent,
  createdAt: row.created_at,
  updatedAt: row.updated_at,
});

const section = (row: any) => {
  const meta: string[] = [];
  if (row.symbol) meta.push(`股票：${row.symbol}`);
  if (row.tags?.length) meta.push(`标签：${row.tags.join("、")}`);
  const body = String(row.body);
  return [
    `### ${row.pinned ? "[置顶] " : ""}${row.title}`,
    ...(meta.length ? [meta.join(" · ")] : []),
    body.length > MAX_NOTE_BODY_CHARS
      ? body.slice(0, MAX_NOTE_BODY_CHARS) + "…"
      : body,
  ].join("\n");
};

/** Prompt text for one account, pinned first. Empty when there is nothing to inject. */
export function renderPersonalNotes(rows: any[]): string {
  const sections: string[] = [];
  let size = 0;
  for (const row of rows.slice(0, MAX_AGENT_NOTES)) {
    const text = section(row);
    if (sections.length && size + text.length + 2 > MAX_NOTE_PROMPT_CHARS)
      break;
    sections.push(text);
    size += text.length + 2;
  }
  return sections.join("\n\n");
}

export class PersonalNoteStore {
  constructor(readonly pool: Pool) {}
  async list(owner: string): Promise<PersonalNote[]> {
    return (
      await this.pool.query(
        "SELECT * FROM personal_notes WHERE owner_id=$1 ORDER BY pinned DESC,updated_at DESC,id LIMIT 500",
        [owner],
      )
    ).rows.map(view);
  }
  async create(owner: string, input: unknown): Promise<PersonalNote> {
    const note = personalNoteInput.parse(input);
    return view(
      (
        await this.pool.query(
          "INSERT INTO personal_notes(id,owner_id,title,body,tags,symbol,pinned,use_in_agent) VALUES($1,$2,$3,$4,$5,$6,$7,$8) RETURNING *",
          [
            randomUUID(),
            owner,
            note.title,
            note.body,
            note.tags,
            note.symbol ?? null,
            note.pinned,
            note.useInAgent,
          ],
        )
      ).rows[0],
    );
  }
  async update(
    owner: string,
    id: string,
    input: unknown,
  ): Promise<PersonalNote> {
    const patch = personalNoteUpdateInput.parse(input);
    return transaction(this.pool, async (c) => {
      const row = (
        await c.query(
          "SELECT * FROM personal_notes WHERE id=$1 AND owner_id=$2 FOR UPDATE",
          [id, owner],
        )
      ).rows[0];
      if (!row) throw new DomainError("not_found", 404);
      // Last write wins: one owner, one notebook, no version token to reconcile.
      const merged = {
        title: patch.title ?? row.title,
        body: patch.body ?? row.body,
        tags: patch.tags ?? row.tags,
        symbol: "symbol" in patch ? patch.symbol : row.symbol,
        pinned: patch.pinned ?? row.pinned,
        useInAgent: patch.useInAgent ?? row.use_in_agent,
      };
      return view(
        (
          await c.query(
            "UPDATE personal_notes SET title=$3,body=$4,tags=$5,symbol=$6,pinned=$7,use_in_agent=$8,updated_at=now() WHERE id=$1 AND owner_id=$2 RETURNING *",
            [
              id,
              owner,
              merged.title,
              merged.body,
              merged.tags,
              merged.symbol ?? null,
              merged.pinned,
              merged.useInAgent,
            ],
          )
        ).rows[0],
      );
    });
  }
  async remove(owner: string, id: string): Promise<{ ok: true }> {
    const removed = (
      await this.pool.query(
        "DELETE FROM personal_notes WHERE id=$1 AND owner_id=$2 RETURNING id",
        [id, owner],
      )
    ).rowCount;
    if (!removed) throw new DomainError("not_found", 404);
    return { ok: true };
  }
  /** Rendered notebook for a personal (never group) thread; empty when unavailable. */
  async forAgent(owner: string, threadId: string): Promise<string> {
    return renderPersonalNotes(
      (
        await this.pool.query(
          `SELECT n.title,n.body,n.tags,n.symbol,n.pinned
        FROM personal_notes n
        JOIN thread_contexts tc ON tc.owner_id=n.owner_id AND tc.thread_id=$2
        JOIN execution_contexts ec ON ec.id=tc.context_id AND ec.owner_id=tc.owner_id AND ec.kind='personal'
        WHERE n.owner_id=$1 AND n.use_in_agent
        ORDER BY n.pinned DESC,n.updated_at DESC,n.id
        LIMIT 20`,
          [owner, threadId],
        )
      ).rows,
    );
  }
}
