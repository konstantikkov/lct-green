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
  /** ISO string per contract; current backend emits a unix timestamp (s). */
  createdAt: string | number;
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
  /** NOT in the frozen contract — some backend builds attach the DWG→DXF
   *  converter/sanitizer log. Rendered only in debug mode when present. */
  conversionLog?: string[];
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

export type AllowedBasis = "site_boundary" | "vegetation_evidence" | "extents_fallback";

export interface ConstraintsResponse {
  siteBoundary: number[][] | null;
  siteBoundarySource: "detected" | "extents_fallback";
  /** How the planting domain was derived (see API_CONTRACT.md).
   *  Optional defensively: older cached constraint payloads omit it. */
  allowedBasis?: AllowedBasis;
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
  /** Iteration-4 contract: 0–100, coarse per-iteration; 100 on COMPLETED.
   *  Optional defensively (older backend builds omit it). */
  progress?: number;
}

// ---------------------------------------------------------------------------
// Judge reports.
// The backend stores both v1 ("JudgeReport" pre-WS2) and v2 ("judge-report-2")
// objects; old iterations keep their old shape forever. The UI therefore uses
// a single defensive type where every version-specific field is optional.
// ---------------------------------------------------------------------------

export type JudgeDecision = "ACCEPT" | "RERUN" | "MANUAL_REVIEW";
export type DimensionStatus = "GOOD" | "IMPROVABLE" | "POOR";

export interface AssessmentDimension {
  status: DimensionStatus;
  evidence: string[]; // evidence IDs from the JudgePacket (q_i_j, cluster_N, …)
}

export type AssessmentKey =
  | "coverage" | "distribution" | "clustering" | "utilization" | "robustnessToUncertainty";

export interface JudgeIssueV2 {
  type: string;
  severity: "LOW" | "MEDIUM" | "HIGH";
  evidenceIds: string[];
  description: string;
}

export interface JudgeParameterChange {
  parameter: string;
  currentValue: number;
  proposedValue: number;
  reason: string;
  evidenceIds: string[];
}

export interface DeterministicComponents {
  coverage: number;
  distribution: number;
  utilizationBalance: number; // 1 − underused-area share
  clusterPenalty: number;     // share of plantings inside dense clusters
}

export interface JudgeReport {
  schemaVersion?: "judge-report-2";
  mode: "llm" | "heuristic_fallback";
  decision: JudgeDecision;
  confidence?: number;
  assessment?: Partial<Record<AssessmentKey, AssessmentDimension>>;
  /** v1: string[]; v2: structured issues. */
  issues?: (string | JudgeIssueV2)[];
  parameterChanges?: JudgeParameterChange[];
  summary?: string;
  deterministicComponents?: DeterministicComponents;
  promptVersion?: string | null;
  model?: string | null;
  temperature?: number | null;
  inputSchemaVersion?: string;
  judgeInputHash?: string;
  // v1 / backward-compatible keys
  score?: { overall: number; coverage: number; distribution: number; confidence: number };
  reasoningSummary?: string;
  recommendedParameterChanges?: Record<string, number | null>;
  llmModel?: string | null;
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
  // WS2 iteration-record additions (absent on old records).
  judgeSkipped?: "hard_violations" | null;
  deterministicRepair?: { removed: number; passes: number } | null;
  needsManualReview?: boolean;
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

// ---------------------------------------------------------------------------
// Admin dashboard & compliance audit (iteration 3) — docs/API_CONTRACT.md.
// ---------------------------------------------------------------------------

export interface ProjectSummary {
  id: string;
  name: string;
  fileName: string;
  createdAt: number;
  state: string;
  format: "DWG" | "DXF" | null;
  units: string | null;
  entityCount: number | null;
  layerCount: number | null;
  uncertainLayers: number | null;
  result: {
    iterationCount: number;
    treeCount: number | null;
    shrubCount: number | null;
    hardViolations: number;
    coverageScore: number | null;
    distributionScore: number | null;
    judgeDecision: string | null;
  } | null;
  /** href download paths */
  exports: { dxf: string; report: string } | null;
}

export interface ComplianceDocStat {
  checks: number;
  passed: number;
  failed: number;
}

export interface ComplianceRuleStat extends ComplianceDocStat {
  document: string;
  effectiveSource: string;
  requiredM: number | null;
}

export interface CompliancePlacement {
  id: string;
  kind: string;
  speciesId: string;
  position: [number, number];
  verdict: "COMPLIANT" | "VIOLATIONS";
  failingRules: string[];
  checks: ExplanationCheck[];
}

export interface ComplianceAudit {
  iterationId: string;
  rulesetVersion: string;
  documentDerivedRules: number;
  verifiedRules: number;
  assumedRules: number;
  summary: { placements: number; compliant: number; withViolations: number; checksTotal: number };
  byDocument: Record<string, ComplianceDocStat>;
  byRule: Record<string, ComplianceRuleStat>;
  placements: CompliancePlacement[];
}

// ---------------------------------------------------------------------------
// Regulations feature (WS4) — transcribed 1:1 from docs/API_CONTRACT.md.
// ---------------------------------------------------------------------------

export type DocStatus = "UPLOADED" | "PARSED" | "EXTRACTED" | "FAILED";
export type RuleCandidateStatus =
  | "EXTRACTED" | "NEEDS_REVIEW" | "VERIFIED" | "ACTIVE" | "DEPRECATED" | "REJECTED";

export interface RegDocument {
  id: string;
  fileName: string;
  title: string | null;
  documentNumber: string | null;
  jurisdiction: string | null;
  publicationDate: string | null;
  effectiveDate: string | null;
  version: string;
  fileHash: string;
  status: DocStatus;
  uploadedAt: number;
  pageCount: number | null;
  fragmentCount: number;
  candidateCounts: Record<RuleCandidateStatus, number>;
  parseLog: string[];
}

export interface Fragment {
  id: string;
  page: number | null;
  hierarchyPath: string;
  kind: "clause" | "table" | "note" | "heading";
  text: string;
  order: number;
}

export interface RuleCandidate {
  id: string;
  documentId: string;
  ruleVersion: number;
  source: {
    documentId: string;
    documentVersion: string;
    page: number | null;
    hierarchyPath: string;
    fragmentIds: string[];
    sourceText: string;
  };
  applicability: {
    jurisdiction: string | null;
    plantType: string[];
    objectTypes: string[];
    conditions: string[];
  };
  constraint: {
    type: "MIN_DISTANCE" | "MIN_SPACING" | "OTHER";
    subject: string;
    referenceObject: string;
    operator: string;
    value: number | null;
    unit: string | null;
  };
  exceptions: string[];
  references: string[];
  machineExecutable: boolean;
  confidence: number;
  status: RuleCandidateStatus;
  verification: {
    verdict: "SUPPORTED" | "PARTIALLY_SUPPORTED" | "UNSUPPORTED" | "AMBIGUOUS" | null;
    numericLiteralFound: boolean;
    notes: string | null;
    model: string | null;
    promptVersion: string | null;
  } | null;
  reviewer: string | null;
  reviewDate: number | null;
  extraction: { model: string; promptVersion: string };
}

export interface CandidateReviewPatch {
  action: "approve" | "reject" | "edit";
  edits?: {
    constraint?: Partial<RuleCandidate["constraint"]>;
    applicability?: Partial<RuleCandidate["applicability"]>;
  };
  reviewer?: string;
}
