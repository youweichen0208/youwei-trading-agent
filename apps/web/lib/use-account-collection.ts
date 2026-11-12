"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, message } from "./client";

type CollectionState<T> = {
  ownerId: string | null;
  items: T[];
  status: "loading" | "ready" | "error";
  error: string;
};

/** Keep failed loads distinct from empty lists and discard stale account responses. */
export function useAccountCollection<T>(path: string, ownerId: string | null) {
  const owner = useRef(ownerId);
  owner.current = ownerId;
  const generation = useRef(0);
  const [state, setState] = useState<CollectionState<T>>({
    ownerId,
    items: [],
    status: "loading",
    error: "",
  });
  const reload = useCallback(async () => {
    if (!ownerId) return;
    const request = ++generation.current;
    setState((previous) => ({
      ownerId,
      items: previous.ownerId === ownerId ? previous.items : [],
      status: "loading",
      error: "",
    }));
    try {
      const items = await api<T[]>(path);
      if (owner.current !== ownerId || request !== generation.current) return;
      setState({ ownerId, items, status: "ready", error: "" });
    } catch (error) {
      if (owner.current !== ownerId || request !== generation.current) return;
      setState((previous) => ({
        ...previous,
        status: "error",
        error: message(error),
      }));
    }
  }, [ownerId, path]);
  useEffect(() => {
    if (ownerId) void reload();
    else setState({ ownerId: null, items: [], status: "loading", error: "" });
    return () => {
      generation.current++;
    };
  }, [ownerId, reload]);
  const replace = useCallback(
    (items: T[]) => {
      if (!ownerId || owner.current !== ownerId) return;
      // A successful mutation supersedes any earlier list request.
      generation.current++;
      setState({ ownerId, items, status: "ready", error: "" });
    },
    [ownerId],
  );
  const current: CollectionState<T> =
    state.ownerId === ownerId
      ? state
      : { ownerId, items: [], status: "loading", error: "" };
  return { ...current, reload, replace };
}
