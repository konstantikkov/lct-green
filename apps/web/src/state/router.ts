import { loadAudit, newProject, openProject } from "./actions";
import { getState, setState, subscribeStore, type AppState } from "./store";

/**
 * Hand-rolled history routing (no router lib).
 *
 * URL map:
 *   /                 — dashboard (start screen)
 *   /p/<projectId>    — project workspace (PLAN)
 *   /p/<id>/runs      — project RUNS view
 *   /p/<id>/audit     — project compliance audit
 *   /regulations      — regulations workspace (global)
 *   /quality          — eval-results view (global)
 *
 * Direction 1 (state → URL): a store subscription derives the canonical path
 * from (nav, document) and pushes it whenever it changes — so every existing
 * action (setNav, openProject, newProject, …) updates the URL for free.
 * Direction 2 (URL → state): popstate / initial deep link parse the path and
 * replay it through the existing openProject flow.
 */

function pathFor(s: AppState): string {
  if (s.nav === "regulations") return "/regulations";
  if (s.nav === "quality") return "/quality";
  const id = s.document?.projectId;
  if (!id) return "/";
  if (s.nav === "runs") return `/p/${encodeURIComponent(id)}/runs`;
  if (s.nav === "audit") return `/p/${encodeURIComponent(id)}/audit`;
  return `/p/${encodeURIComponent(id)}`;
}

/** True while a URL is being applied to the state (suppresses push-back). */
let applying = false;

async function applyPath(pathname: string): Promise<void> {
  applying = true;
  try {
    if (pathname === "/regulations") {
      setState({ nav: "regulations" });
      return;
    }
    if (pathname === "/quality") {
      setState({ nav: "quality" });
      return;
    }

    const m = /^\/p\/([^/]+)(?:\/(runs|audit))?\/?$/.exec(pathname);
    if (m) {
      const id = decodeURIComponent(m[1]);
      const sub = (m[2] ?? "plan") as "plan" | "runs" | "audit";
      if (getState().document?.projectId !== id) {
        // Deep link / foreign history entry: load via the normal flow.
        await openProject(id);
        if (getState().document?.projectId !== id) {
          // Unknown / deleted project id → dashboard with a small note.
          history.replaceState(null, "", "/");
          setState({
            routeError: "Проект не найден — возможно, он был удалён или ссылка устарела.",
            uploadError: null,
            nav: "plan",
          });
          return;
        }
      }
      setState({ nav: sub });
      if (sub === "audit") void loadAudit();
      return;
    }

    // Everything else (incl. "/") is the dashboard.
    if (getState().document) newProject();
    else setState({ nav: "plan" });
  } finally {
    applying = false;
  }
}

/** Installs popstate handling, applies the initial deep link and starts the
 *  state → URL sync. Call once from main.tsx. */
export function initRouter(): void {
  window.addEventListener("popstate", () => {
    void applyPath(location.pathname);
  });

  subscribeStore(() => {
    if (applying) return;
    const s = getState();
    // While a project is being opened (or a file uploaded) the document is
    // not installed yet — don't rewrite a /p/<id> deep link back to "/".
    if (s.projectLoadingId || s.uploading) return;
    const path = pathFor(s);
    if (path !== location.pathname) history.pushState(null, "", path);
  });

  if (location.pathname !== "/") void applyPath(location.pathname);
}
