import { useSyncExternalStore } from "react";

export type Theme = "light" | "dark";

const STORAGE_KEY = "greenplan.theme";

function readInitial(): Theme {
  try {
    return localStorage.getItem(STORAGE_KEY) === "dark" ? "dark" : "light";
  } catch {
    return "light";
  }
}

let theme: Theme = readInitial();
const listeners = new Set<() => void>();

export function getTheme(): Theme {
  return theme;
}

export function setTheme(next: Theme) {
  if (next === theme) return;
  theme = next;
  document.documentElement.classList.toggle("dark", next === "dark");
  try {
    localStorage.setItem(STORAGE_KEY, next);
  } catch {
    /* storage unavailable — non-fatal */
  }
  listeners.forEach((l) => l());
}

export function toggleTheme() {
  setTheme(theme === "dark" ? "light" : "dark");
}

export function useTheme(): Theme {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => theme,
    () => theme,
  );
}
