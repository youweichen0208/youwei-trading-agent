"use client";
import { useEffect, useState } from "react";
import { experimentalEvaluationIdSchema } from "@yta/contracts";
import type { Me } from "./api";

export function useReportSelection(me: Me) {
  const key = `yta:report-comparison:${me.user.id}:${me.data_mode}`;
  const [ids, setIds] = useState<string[]>([]);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    setIds([]); setReady(false); setError("");
    try {
      const saved: unknown = JSON.parse(sessionStorage.getItem(key) ?? "[]");
      if (Array.isArray(saved) && saved.length <= 10 && new Set(saved).size === saved.length
        && saved.every(id => experimentalEvaluationIdSchema.safeParse(id).success)) setIds(saved);
    } catch {setError("浏览器无法保存比较选择，请允许本页使用会话存储后重试。");}
    setReady(true);
  }, [key]);
  function update(next: string[]) {
    try {sessionStorage.setItem(key, JSON.stringify(next)); setIds(next); setError("");}
    catch {setError("无法保存比较选择，请检查浏览器会话存储设置。");}
  }
  return {ids, ready, error, clear: () => update([]), toggle: (id: string) => {
    if (!ready || !experimentalEvaluationIdSchema.safeParse(id).success) return;
    if (ids.includes(id)) update(ids.filter(value => value !== id));
    else if (ids.length < 10) update([...ids, id]);
  }};
}
