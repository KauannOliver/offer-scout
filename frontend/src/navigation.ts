import { useCallback, useEffect, useState } from "react";

export function useRoute<T extends string>(allowed: readonly T[], fallback: T) {
  const read = useCallback(() => {
    const route = location.pathname.split("/")[1] as T;
    return allowed.includes(route) ? route : fallback;
  }, [allowed, fallback]);
  const [route, update] = useState<T>(read);
  useEffect(() => {
    const listener = () => update(read());
    window.addEventListener("popstate", listener);
    return () => window.removeEventListener("popstate", listener);
  }, [read]);
  return [route, (value: T) => { history.pushState(null, "", `/${value}${location.search}`); update(value); }] as const;
}

export function useQueryState(key: string, fallback: string) {
  const [value, setValue] = useState(() => new URLSearchParams(location.search).get(key) ?? fallback);
  useEffect(() => {
    const listener = () => setValue(new URLSearchParams(location.search).get(key) ?? fallback);
    window.addEventListener("popstate", listener);
    return () => window.removeEventListener("popstate", listener);
  }, [key, fallback]);
  return [value, (next: string) => {
    const params = new URLSearchParams(location.search);
    if (next === fallback) params.delete(key); else params.set(key, next);
    history.replaceState(null, "", `${location.pathname}?${params}`); setValue(next);
  }] as const;
}
