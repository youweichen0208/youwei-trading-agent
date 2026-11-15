"use client";
import { useEffect, useMemo, useRef, useState } from "react";

/** Baseline reveal speed while the backlog is small, in characters per second. */
const STEADY_RATE = 55;
/** A freshly received chunk is fully revealed within roughly this window. */
const CATCH_UP_MS = 420;

/**
 * Reveal speed, in characters per millisecond, for a buffer of `backlog`
 * characters. A new chunk raises the rate so the whole chunk lands within
 * CATCH_UP_MS; the steady floor keeps short chunks readable.
 */
export function revealRate(backlog: number) {
  return Math.max(STEADY_RATE / 1000, backlog / CATCH_UP_MS);
}

/** Characters to reveal this frame, never more than what is buffered. */
export function revealStep(backlog: number, elapsedMs: number, rate: number) {
  if (backlog <= 0) return 0;
  return Math.min(backlog, Math.max(1, Math.ceil(rate * elapsedMs)));
}

function prefersReducedMotion() {
  return (
    typeof window !== "undefined" &&
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true
  );
}

/**
 * Presents target text progressively. The first render shows the whole string,
 * so restored history is never re-typed; only text that grows while `streaming`
 * is revealed character by character. Content that no longer extends what was
 * already shown (final answer replacing the partial message, snapshot rollback)
 * snaps instead of replaying.
 */
export function useSmoothText(target: string, streaming: boolean) {
  const characters = useMemo(() => Array.from(target), [target]);
  const [revealed, setRevealed] = useState(characters.length);
  const total = useRef(characters.length);
  const shown = useRef(revealed);
  const previous = useRef("");
  const previousBacklog = useRef(0);
  const rate = useRef(revealRate(0));
  total.current = characters.length;

  const continued = target.startsWith(previous.current);

  useEffect(() => {
    previous.current = target;
  }, [target]);

  useEffect(() => {
    if (!continued || revealed > characters.length)
      setRevealed(characters.length);
  }, [continued, characters.length, revealed]);

  useEffect(() => {
    shown.current = revealed;
  }, [revealed]);

  useEffect(() => {
    if (!streaming || prefersReducedMotion()) {
      setRevealed(total.current);
      return;
    }
    // Runs only while text is pending: a new chunk changes the length and
    // restarts it, and the loop stops itself once it has caught up.
    if (shown.current >= total.current) return;
    let last = performance.now();
    let frame = 0;
    const tick = (now: number) => {
      const backlog = total.current - shown.current;
      if (backlog <= 0) {
        previousBacklog.current = 0;
        last = now;
        return;
      }
      // A larger buffer means a fresh chunk arrived: pace it to clear within
      // the catch-up window instead of trailing further behind.
      if (backlog > previousBacklog.current) rate.current = revealRate(backlog);
      previousBacklog.current = backlog;
      const next = Math.min(
        total.current,
        shown.current + revealStep(backlog, now - last, rate.current),
      );
      shown.current = next;
      setRevealed(next);
      last = now;
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [streaming, characters.length]);

  const count = continued
    ? Math.min(revealed, characters.length)
    : characters.length;
  return {
    text: continued ? characters.slice(0, count).join("") : target,
    typing: streaming && continued && count < characters.length,
  };
}
