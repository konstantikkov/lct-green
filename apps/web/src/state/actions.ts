import { api, ApiError, watchRun } from "../api/client";
import type { Category, GenerateRequest, PlantKind, ProjectDocument } from "../api/types";
import {
  getState, setState, MASSING_DETAIL_STORAGE_KEY,
  type GenOptions, type MobileTab, type NavView, type Selection, type SheetSnap,
  type ViewMode, type WorkspaceTab,
} from "./store";

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** Matches Tailwind's `md` breakpoint — the bottom sheet exists below it. */
const mobileMq = typeof window !== "undefined" && "matchMedia" in window
  ? window.matchMedia("(max-width: 767px)")
  : null;

export function isMobile(): boolean {
  return mobileMq?.matches ?? false;
}

export function setMobileTab(tab: MobileTab) {
  setState({ mobileTab: tab });
}

export function setSheetSnap(snap: SheetSnap) {
  setState({ sheetSnap: snap });
}

/** Raises the sheet to at least `half` (never lowers from `full`). */
function raiseSheet(tab: MobileTab) {
  const snap = getState().sheetSnap;
  setState({ mobileTab: tab, sheetSnap: snap === "full" ? "full" : "half" });
}

/**
 * Selects a workspace tab (the strip under the TopBar drives both the desktop
 * panel and the mobile bottom sheet). Un-collapses the panel; on mobile it
 * also raises the sheet so the tapped section is actually visible.
 */
export function setWorkspaceTab(tab: WorkspaceTab) {
  setState({ workspaceTab: tab, panelCollapsed: false, mobileTab: "panel" });
  if (isMobile()) {
    const snap = getState().sheetSnap;
    if (snap === "peek") setState({ sheetSnap: "half" });
  }
}

export function togglePanelCollapsed() {
  setState((s) => ({ panelCollapsed: !s.panelCollapsed }));
}

export async function loadCapabilities() {
  try {
    setState({ capabilities: await api.getCapabilities() });
  } catch {
    /* backend not up yet — non-fatal */
  }
}

export async function loadStaticData() {
  try {
    setState({ species: await api.getSpecies(), speciesError: null });
  } catch (e) {
    setState({ speciesError: errMsg(e) });
  }
  try {
    setState({ rules: await api.getRules(), rulesError: null });
  } catch (e) {
    setState({ rulesError: errMsg(e) });
  }
}

/** Resets all per-project state and installs `doc` as the active document. */
function installDocument(doc: ProjectDocument) {
  const layerVisibility: Record<string, boolean> = {};
  for (const l of doc.layers) layerVisibility[l.name] = true;
  setState({
    document: doc,
    layerVisibility,
    pipelineState: doc.state,
    uploading: false,
    constraints: null,
    constraintsError: null,
    iterations: [],
    activeIteration: null,
    selection: null,
    explanation: null,
    chunks: null,
    run: null,
    runError: null,
    nav: "plan",
    // Auto-advance: after upload land on Интерпретация.
    workspaceTab: "interpretation",
    panelCollapsed: false,
    mobileTab: "panel",
    audit: null,
    auditStatus: "idle",
    auditError: null,
    routeError: null,
    // Wizard state is per-project; uploadFile re-opens it explicitly.
    wizardOpen: false,
    wizardStep: 1,
    wizardCandidates: [],
    wizardNotice: null,
    wizardBusy: false,
    wizardBusyLabel: null,
    wizardCandidateBusyId: null,
    wizardCandidateErrors: {},
  });
  // Static data may have failed earlier if backend was down; retry now.
  const s = getState();
  if (!s.species || !s.rules) void loadStaticData();
  if (!s.capabilities) void loadCapabilities();
  // Progressive disclosure: constraint zones are fetched automatically after
  // the document loads (GET /constraints computes on demand) — the sidebar
  // shows a quiet "Building constraint zones…" line meanwhile.
  void buildConstraints();
}

export async function uploadFile(file: File) {
  setState({ uploading: true, uploadError: null, routeError: null });
  try {
    const doc = await api.uploadProject(file);
    installDocument(doc);
    // The step-by-step wizard opens automatically after a successful upload
    // (never when opening an existing project).
    setState({ wizardOpen: true, wizardStep: 1 });
    void loadProjects(); // keep the shared list fresh
    void loadProjectSummaries();
  } catch (e) {
    const code = e instanceof ApiError ? e.code : null;
    setState({ uploading: false, uploadError: { code, message: errMsg(e) } });
  }
}

export async function loadProjects() {
  try {
    setState({ projects: await api.listProjects(), projectsError: null });
  } catch (e) {
    setState({ projectsError: errMsg(e) });
  }
}

/** Rich listing for the admin dashboard (start screen). */
export async function loadProjectSummaries() {
  try {
    setState({ summaries: await api.getProjectsSummary(), summariesError: null });
  } catch (e) {
    setState({ summariesError: errMsg(e) });
  }
}

/** Opens an existing project from the shared list. 404s are non-fatal. */
export async function openProject(projectId: string) {
  if (getState().projectLoadingId) return;
  setState({ projectLoadingId: projectId, uploadError: null });
  try {
    const doc = await api.getDocument(projectId);
    installDocument(doc); // auto-fetches constraints
  } catch (e) {
    const code = e instanceof ApiError ? e.code : null;
    setState({ projectLoadingId: null, uploadError: { code, message: errMsg(e) } });
    return;
  }
  // Iterations — may legitimately be empty or 404.
  try {
    const iterations = await api.getIterations(projectId);
    if (getState().document?.projectId === projectId && iterations.length > 0) {
      setState({ iterations });
      const selected = iterations.find((i) => i.selected) ?? iterations[iterations.length - 1];
      if (selected) await loadIteration(selected.iterationId);
    }
  } catch {
    /* no runs yet — fine */
  }
  setState({ projectLoadingId: null });
}

/** Returns to the start screen (admin dashboard). Also used by the router
 *  for `/` and by the TopBar logo ("GreenPlan" → home). */
export function newProject() {
  setState({
    document: null,
    nav: "plan",
    uploadError: null,
    pipelineState: null,
    constraints: null,
    constraintsError: null,
    iterations: [],
    activeIteration: null,
    selection: null,
    explanation: null,
    chunks: null,
    run: null,
    runError: null,
    layerVisibility: {},
    workspaceTab: "interpretation",
    panelCollapsed: false,
    audit: null,
    auditStatus: "idle",
    auditError: null,
    wizardOpen: false,
    wizardStep: 1,
    wizardCandidates: [],
    wizardNotice: null,
    wizardBusy: false,
    wizardBusyLabel: null,
    wizardCandidateBusyId: null,
    wizardCandidateErrors: {},
  });
  void loadProjects();
  void loadProjectSummaries();
}

/** DELETE /api/projects/{id} — removes the row from both cached lists. */
export async function deleteProject(projectId: string) {
  try {
    await api.deleteProject(projectId);
    setState((s) => ({
      summaries: s.summaries?.filter((p) => p.id !== projectId) ?? null,
      projects: s.projects?.filter((p) => p.id !== projectId) ?? null,
      routeError: null,
    }));
    void loadProjectSummaries(); // refresh from the source of truth
  } catch (e) {
    setState({ routeError: `Не удалось удалить проект: ${errMsg(e)}` });
  }
}

export function setViewMode(mode: ViewMode) {
  setState({ viewMode: mode });
}

/** Simple ⇄ Detailed 3D massing; persisted like the theme. */
export function setMassingDetail(detailed: boolean) {
  setState({ massingDetail: detailed });
  try {
    localStorage.setItem(MASSING_DETAIL_STORAGE_KEY, detailed ? "detailed" : "simple");
  } catch {
    /* storage unavailable — non-fatal */
  }
}

export async function overrideLayerCategory(layerName: string, category: Category) {
  const doc = getState().document;
  if (!doc) return;
  try {
    const updated = await api.patchLayerClassification(doc.projectId, layerName, category);
    setState({
      document: {
        ...doc,
        layers: doc.layers.map((l) => (l.name === layerName ? updated : l)),
      },
      // Contract: classification override invalidates cached constraints.
      constraints: null,
    });
    // Rebuild automatically so the overlays stay in sync without a button hunt.
    void buildConstraints();
  } catch (e) {
    setState({ constraintsError: `Layer override failed: ${errMsg(e)}` });
  }
}

export function toggleLayerVisibility(layerName: string) {
  const s = getState();
  setState({
    layerVisibility: { ...s.layerVisibility, [layerName]: !s.layerVisibility[layerName] },
  });
}

/** Sets visibility for a whole semantic group of CAD layers at once. */
export function setLayersVisibility(layerNames: string[], visible: boolean) {
  const s = getState();
  const next = { ...s.layerVisibility };
  for (const name of layerNames) next[name] = visible;
  setState({ layerVisibility: next });
}

export function setNav(nav: NavView) {
  setState({ nav });
}

export function setGenOptions(patch: Partial<GenOptions>) {
  const s = getState();
  setState({ genOptions: { ...s.genOptions, ...patch } });
}

/** Builds a GenerateRequest from shared options and starts the run. */
export function generateNow() {
  const s = getState();
  if (!s.document || s.runActive) return;
  const opts = s.genOptions;
  const allIds = (s.species ?? []).map((sp) => sp.id);
  const speciesIds = opts.speciesIds ?? allIds;
  const chosen = (s.species ?? []).filter((sp) => speciesIds.includes(sp.id));
  const plantKinds = [...new Set(chosen.map((sp) => sp.kind))] as PlantKind[];
  const tc = opts.targetCount.trim() === "" ? null : Number(opts.targetCount);
  void startGeneration({
    plantKinds: plantKinds.length > 0 ? plantKinds : ["TREE"],
    speciesIds,
    strategy: opts.strategy,
    targetCount: tc != null && Number.isFinite(tc) ? tc : null,
    seed: Number.isFinite(Number(opts.seed)) ? Number(opts.seed) : 42,
    maxIterations: Math.max(1, Number(opts.maxIterations) || 3),
    useLlmJudge: opts.useLlmJudge,
  });
}

/** Loads the bundled example drawing and uploads it as a new project. */
export async function openExample() {
  if (getState().uploading) return;
  setState({ uploading: true, uploadError: null });
  try {
    const res = await fetch("/example/synthetic_site.dxf");
    if (!res.ok) throw new Error(`example fetch failed (HTTP ${res.status})`);
    const blob = await res.blob();
    const file = new File([blob], "synthetic_site.dxf", { type: "application/dxf" });
    await uploadFile(file);
  } catch (e) {
    setState({ uploading: false, uploadError: { code: null, message: errMsg(e) } });
  }
}

export function setOverlay(key: keyof ReturnType<typeof getState>["overlays"], value: boolean) {
  const s = getState();
  setState({ overlays: { ...s.overlays, [key]: value } });
  if (key === "chunkGrid" && value && !s.chunks && s.document) void loadChunks();
}

async function loadChunks() {
  const doc = getState().document;
  if (!doc) return;
  try {
    setState({ chunks: await api.getChunks(doc.projectId) });
  } catch {
    /* non-fatal */
  }
}

export async function buildConstraints() {
  const doc = getState().document;
  if (!doc) return;
  setState({ constraintsLoading: true, constraintsError: null });
  try {
    const constraints = await api.getConstraints(doc.projectId);
    setState({ constraints, constraintsLoading: false });
  } catch (e) {
    setState({ constraintsLoading: false, constraintsError: errMsg(e) });
  }
}

export async function startGeneration(req: GenerateRequest) {
  const doc = getState().document;
  if (!doc) return;
  setState({
    runActive: true, runError: null, run: null,
    // Auto-advance: jump to Генерация to show the stage checklist.
    workspaceTab: "generate", panelCollapsed: false, mobileTab: "panel",
    // Previous audit (if any) refers to the previous iteration.
    audit: null, auditStatus: "idle", auditError: null,
  });
  // On mobile, surface run progress in the sheet.
  if (isMobile()) raiseSheet("panel");
  try {
    const { runId } = await api.generate(doc.projectId, req);
    const final = await watchRun(doc.projectId, runId, (run) => {
      setState({ run, pipelineState: run.state });
    });
    if (final.state === "FAILED") {
      setState({ runActive: false, runError: final.error ?? "Run failed" });
      return;
    }
    await loadIterations();
    // Auto-advance: land on Результат once the run completed. Inside the
    // wizard, the Генерация step advances to Итоги automatically.
    setState((s) => ({
      runActive: false,
      workspaceTab: "result" as const,
      ...(s.wizardOpen && s.wizardStep === 3 ? { wizardStep: 4 as const } : {}),
    }));
    void loadProjectSummaries(); // dashboard result columns changed
  } catch (e) {
    setState({ runActive: false, runError: errMsg(e) });
  }
}

export async function loadIterations() {
  const doc = getState().document;
  if (!doc) return;
  try {
    const iterations = await api.getIterations(doc.projectId);
    setState({ iterations });
    const selected = iterations.find((i) => i.selected) ?? iterations[iterations.length - 1];
    if (selected) await loadIteration(selected.iterationId);
  } catch (e) {
    setState({ runError: `Failed to load iterations: ${errMsg(e)}` });
  }
}

export async function loadIteration(iterationId: string) {
  const doc = getState().document;
  if (!doc) return;
  setState({ iterationLoadingId: iterationId });
  try {
    const detail = await api.getIteration(doc.projectId, iterationId);
    setState({ activeIteration: detail, iterationLoadingId: null });
  } catch (e) {
    setState({ iterationLoadingId: null, runError: `Failed to load iteration: ${errMsg(e)}` });
  }
}

export function select(selection: Selection) {
  setState({ selection, explanation: null, explanationError: null });
  // On mobile, picking something in the viewer opens the Inspector tab.
  if (selection && isMobile()) raiseSheet("inspector");
  if (selection?.type === "placement") void loadExplanation(selection.placement.id);
}

// --- Compliance audit (iteration 3) -----------------------------------------

/** Opens the dedicated "Аудит соответствия" view and (re)fetches the audit. */
export function openAudit() {
  if (!getState().document) return;
  setState({ nav: "audit" });
  void loadAudit();
}

/** Deterministic re-check is cheap server-side — always fetch fresh. */
export async function loadAudit() {
  const doc = getState().document;
  if (!doc) return;
  setState({ auditStatus: "loading", auditError: null });
  try {
    const audit = await api.getCompliance(doc.projectId);
    if (getState().document?.projectId !== doc.projectId) return; // stale
    setState({ audit, auditStatus: "loaded" });
  } catch (e) {
    if (e instanceof ApiError && (e.code === "NO_RESULT" || e.status === 409)) {
      setState({ audit: null, auditStatus: "no_result" });
    } else {
      setState({ auditStatus: "error", auditError: errMsg(e) });
    }
  }
}

/**
 * Audit row click: select that tree on the canvas (existing placement
 * selection) and open the Inspector. Loads the audited iteration first if a
 * different one is active.
 */
export async function selectAuditPlacement(placementId: string) {
  const s = getState();
  if (!s.document || !s.audit) return;
  if (s.activeIteration?.iterationId !== s.audit.iterationId) {
    await loadIteration(s.audit.iterationId);
  }
  const placement = getState().activeIteration?.placements.find((p) => p.id === placementId);
  if (!placement) return;
  setState({ nav: "plan", workspaceTab: "result" });
  select({ type: "placement", placement });
}

async function loadExplanation(placementId: string) {
  const doc = getState().document;
  if (!doc) return;
  setState({ explanationLoading: true, explanationError: null });
  try {
    const explanation = await api.getExplanation(doc.projectId, placementId);
    // Ignore stale responses if the user selected something else meanwhile.
    const sel = getState().selection;
    if (sel?.type === "placement" && sel.placement.id === placementId) {
      setState({ explanation, explanationLoading: false });
    }
  } catch (e) {
    setState({ explanationLoading: false, explanationError: errMsg(e) });
  }
}
