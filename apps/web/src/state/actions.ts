import { api, ApiError, watchRun } from "../api/client";
import type { Category, GenerateRequest } from "../api/types";
import { getState, setState, type Selection } from "./store";

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
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

export async function uploadFile(file: File) {
  setState({ uploading: true, uploadError: null });
  try {
    const doc = await api.uploadProject(file);
    const layerVisibility: Record<string, boolean> = {};
    for (const l of doc.layers) layerVisibility[l.name] = true;
    setState({
      document: doc,
      layerVisibility,
      pipelineState: doc.state,
      uploading: false,
      constraints: null,
      iterations: [],
      activeIteration: null,
      selection: null,
      explanation: null,
      chunks: null,
      run: null,
      runError: null,
    });
    // Static data may have failed earlier if backend was down; retry now.
    const s = getState();
    if (!s.species || !s.rules) void loadStaticData();
    if (!s.capabilities) void loadCapabilities();
  } catch (e) {
    const code = e instanceof ApiError ? e.code : null;
    setState({ uploading: false, uploadError: { code, message: errMsg(e) } });
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
  setState({ runActive: true, runError: null, run: null });
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
    setState({ runActive: false });
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
  if (selection?.type === "placement") void loadExplanation(selection.placement.id);
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
