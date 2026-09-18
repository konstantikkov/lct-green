// Types transcribed 1:1 from docs/API_CONTRACT.md (frozen contract).

export type Category =
  | "SITE_BOUNDARY" | "BUILDING" | "ROAD" | "CURB" | "SIDEWALK"
  | "UTILITY" | "UTILITY_WATER" | "UTILITY_GAS" | "UTILITY_ELECTRICITY"
  | "UTILITY_SEWER" | "UTILITY_HEAT" | "UTILITY_DRAINAGE" | "UTILITY_COMMUNICATION"
  | "EXISTING_TREE" | "EXISTING_SHRUB" | "GREEN_AREA" | "PARKING"
  | "ACCESS_ROUTE" | "FENCE" | "LIGHTING" | "ANNOTATION" | "UNKNOWN";

export const ALL_CATEGORIES: Category[] = [
  "SITE_BOUNDARY", "BUILDING", "ROAD", "CURB", "SIDEWALK",
  "UTILITY", "UTILITY_WATER", "UTILITY_GAS", "UTILITY_ELECTRICITY",
  "UTILITY_SEWER", "UTILITY_HEAT", "UTILITY_DRAINAGE", "UTILITY_COMMUNICATION",
  "EXISTING_TREE", "EXISTING_SHRUB", "GREEN_AREA", "PARKING",
  "ACCESS_ROUTE", "FENCE", "LIGHTING", "ANNOTATION", "UNKNOWN",
];

export type PipelineState =
  | "UPLOADED" | "CONVERTING" | "PARSING" | "CLASSIFYING"
  | "BUILDING_CONSTRAINTS" | "GENERATING_CANDIDATES" | "OPTIMIZING"
  | "VALIDATING" | "JUDGING" | "RERUNNING" | "COMPLETED" | "FAILED";

export type Strategy = "balanced" | "maximum_trees" | "maximum_canopy" | "street_greening";
export type PlantKind = "TREE" | "SHRUB";

export const CATEGORY_COLORS: Record<string, string> = {
  SITE_BOUNDARY: "#f59e0b", BUILDING: "#94a3b8", ROAD: "#64748b",
  CURB: "#7c8aa0", SIDEWALK: "#a8b3c4", UTILITY: "#e879f9",
  UTILITY_WATER: "#38bdf8", UTILITY_GAS: "#fbbf24", UTILITY_ELECTRICITY: "#f472b6",
  UTILITY_SEWER: "#a78bfa", UTILITY_HEAT: "#fb923c", UTILITY_DRAINAGE: "#22d3ee",
  UTILITY_COMMUNICATION: "#c084fc", EXISTING_TREE: "#4ade80", EXISTING_SHRUB: "#86efac",
  GREEN_AREA: "#166534", PARKING: "#475569", ACCESS_ROUTE: "#818cf8",
  FENCE: "#9ca3af", LIGHTING: "#fde047", ANNOTATION: "#556070", UNKNOWN: "#6b7280",
};

export interface Capabilities {
  dwgConverter: { available: boolean; kind: string | null; path: string | null };
  localLlm: { enabled: boolean; baseUrl: string | null; model: string | null; reachable: boolean };
}

export interface ProjectListItem {
  id: string;
  name: string;
  fileName: string;
  createdAt: string;
  state: PipelineState;
}

export interface LayerClassification {
  category: Category;
  confidence: number; // 0..1
  source: "rule" | "geometry" | "llm" | "user";
  requiresReview: boolean;
}

export interface LayerInfo {
  name: string;
  entityCount: number;
  color: string;
  classification: LayerClassification;
}

export interface EntityRender {
  id: string;
  sourceHandle: string | null;
  type: string;
  layer: string;
  kind: "polyline" | "polygon" | "point" | "text";
  points: number[]; // flat [x0,y0,x1,y1,...] meters
  closed?: boolean;
  position?: [number, number];
  text?: string;
  radius?: number;
}

export interface ProjectDocument {
  projectId: string;
  fileName: string;
  format: "DXF" | "DWG";
  cadVersion: string;
  units: string;
  unitsScaleToMeters: number;
  entityCount: number;
  layerCount: number;
  extents: { min: [number, number]; max: [number, number] };
  state: PipelineState;
  layers: LayerInfo[];
  entities: EntityRender[];
}

export interface ChunkInfo {
  chunkId: string;
  bbox: [number, number, number, number]; // minx,miny,maxx,maxy
  summary: Record<string, number>;
  textLabels: string[];
}

export interface RegulatoryRule {
  id: string;
  enabled: boolean;
  appliesTo: { plantingType: PlantKind[]; obstacleType: Category[] };
  constraint: {
    type: "min_distance" | "min_spacing" | "inside" | "species_blacklist";
    distanceM: number | null;
    units: "m";
  };
  source: {
    document: string;
    documentDate: string | null;
    clause: string | null;
    title: string;
    url: string | null;
    excerpt: string | null;
  };
  status: "verified" | "missing_source_data";
  severity: "hard" | "recommended";
  assumption: {
    assumedDistanceM: number | null;
    enabled: boolean;
    note: string;
  } | null;
  effectiveDistanceM: number | null;
  effectiveSource: "verified" | "assumption" | "inactive";
}

export interface SpeciesInfo {
  id: string;
  kind: PlantKind;
  displayName: string;
  displayNameRu: string;
  parameters: { crownRadiusM: number; matureHeightM: number; rootZoneRadiusM: number };
  regulatoryStatus: "allowed" | "unknown";
}

export interface PolygonWithHoles {
  shell: number[];
  holes: number[][];
}

export interface ExclusionZone {
  id: string;
  ruleId: string;
  generatedFrom: string;
  sourceLayer: string;
  obstacleCategory: Category;
  distanceM: number;
  effectiveSource: "verified" | "assumption";
  polygon: PolygonWithHoles;
  source: { document: string; clause: string | null; status: string };
}

export interface ConstraintsResponse {
  siteBoundary: number[][] | null;
  siteBoundarySource: "detected" | "extents_fallback";
  exclusionZones: ExclusionZone[];
  allowedArea: { polygons: PolygonWithHoles[]; areaM2: number };
  repairLog: string[];
  inactiveRules: { ruleId: string; reason: string }[];
}

export interface GenerateRequest {
  plantKinds: PlantKind[];
  speciesIds: string[];
  strategy: Strategy;
  targetCount: number | null;
  seed: number;
  maxIterations: number;
  useLlmJudge: boolean;
}

export interface RunStage {
  stage: PipelineState;
  status: "pending" | "active" | "done" | "error";
  detail: string;
}

export interface RunStatus {
  runId: string;
  state: PipelineState;
  stages: RunStage[];
  currentIteration: number;
  error: string | null;
}

export interface JudgeReport {
  mode: "llm" | "heuristic_fallback";
  decision: "ACCEPT" | "RERUN";
  score: { overall: number; coverage: number; distribution: number; confidence: number };
  issues: string[];
  recommendedParameterChanges: Record<string, number | null>;
  reasoningSummary: string;
  llmModel: string | null;
}

export interface IterationSummary {
  iterationId: string;
  index: number;
  runId: string;
  selected: boolean;
  parameters: Record<string, number | string>;
  seed: number;
  metrics: {
    treeCount: number;
    shrubCount: number;
    validCount: number;
    coverageScore: number;
    distributionScore: number;
    canopyAreaM2: number;
    hardViolations: number;
  };
  judge: JudgeReport | null;
  runtimeMs: number;
}

export interface Placement {
  id: string;
  kind: PlantKind;
  speciesId: string;
  position: [number, number];
  crownRadiusM: number;
  score: number;
  distances: Record<string, number | null>;
}

export interface ValidationReport {
  valid: boolean;
  hardViolations: { plantingId: string; ruleId: string; actual: number; requiredM: number; obstacle: string }[];
  warnings: string[];
  metrics: { plantingCount: number; validCount: number; coverageScore: number; distributionScore: number };
}

export type IterationDetail = IterationSummary & {
  placements: Placement[];
  rejectedCandidates: [number, number][];
  candidateCount: number;
  validation: ValidationReport;
};

export interface ExplanationCheck {
  ruleId: string;
  result: "passed" | "n/a";
  actualM: number | null;
  requiredM: number | null;
  comparator: ">=";
  obstacleCategory: string;
  effectiveSource: "verified" | "assumption";
  source: { document: string; clause: string | null; title: string; status: string };
}

export interface Explanation {
  plantingId: string;
  position: [number, number];
  speciesId: string;
  iteration: number;
  summary: string;
  llmNarrative: string | null;
  checks: ExplanationCheck[];
}
