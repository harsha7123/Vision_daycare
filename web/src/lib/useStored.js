import { useEffect, useState } from "react";

const isPlainObject = (v) => v && typeof v === "object" && !Array.isArray(v);

/** useState persisted to localStorage (per browser); falls back to memory when storage is blocked.
 *  Stored objects are merged over `initial` so new settings keys get their defaults. */
export function useStored(key, initial) {
  const [value, setValue] = useState(() => {
    try {
      const raw = localStorage.getItem(key);
      if (!raw) return initial;
      const parsed = JSON.parse(raw);
      return isPlainObject(initial) && isPlainObject(parsed) ? { ...initial, ...parsed } : parsed;
    } catch {
      return initial;
    }
  });
  useEffect(() => {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* storage blocked */ }
  }, [key, value]);
  return [value, setValue];
}
