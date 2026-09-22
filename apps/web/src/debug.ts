/**
 * Debug mode gate: `?debug=1` in the URL or `localStorage["greenplan.debug"]`
 * set to "1"/"true". Evaluated once at module load — debug mode is a
 * developer switch, not reactive UI state.
 */
export const DEBUG_STORAGE_KEY = "greenplan.debug";

function detectDebug(): boolean {
  try {
    if (typeof window === "undefined") return false;
    const qs = new URLSearchParams(window.location.search);
    const q = qs.get("debug");
    if (q === "1" || q === "true") return true;
    const stored = localStorage.getItem(DEBUG_STORAGE_KEY);
    return stored === "1" || stored === "true";
  } catch {
    return false;
  }
}

export const isDebug: boolean = detectDebug();
