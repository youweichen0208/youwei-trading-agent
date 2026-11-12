"use client";
import { useEffect, useMemo, useRef } from "react";

/** A response may update the UI only while its account or interaction still exists. */
export function useResponseScope() {
  const generation = useRef(0);
  const scope = useMemo(() => {
    const capture = () => {
      const version = generation.current;
      return () => generation.current === version;
    };
    const invalidate = () => {
      generation.current++;
    };
    return {
      capture,
      invalidate,
      begin: () => {
        invalidate();
        return capture();
      },
    };
  }, []);
  useEffect(() => () => scope.invalidate(), [scope]);
  return scope;
}
