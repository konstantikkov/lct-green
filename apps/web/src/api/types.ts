// Types transcribed 1:1 from docs/API_CONTRACT.md (frozen contract).

export type Category =
  | "SITE_BOUNDARY" | "BUILDING" | "ROAD" | "CURB" | "SIDEWALK"
  | "UTILITY" | "UTILITY_WATER" | "UTILITY_GAS" | "UTILITY_ELECTRICITY"
  | "UTILITY_SEWER" | "UTILITY_HEAT" | "UTILITY_DRAINAGE" | "UTILITY_COMMUNICATION"
  | "POWER_LINE_OVERHEAD"
  | "EXISTING_TREE" | "EXISTING_SHRUB" | "GREEN_AREA" | "PARKING"
  | "ACCESS_ROUTE" | "FENCE" | "LIGHTING" | "RETAINING_WALL" | "UNDERGROUND_PARKING"
  | "ANNOTATION" | "UNKNOWN";

export const ALL_CATEGORIES: Category[] = [
  "SITE_BOUNDARY", "BUILDING", "ROAD", "CURB", "SIDEWALK",
  "UTILITY", "UTILITY_WATER", "UTILITY_GAS", "UTILITY_ELECTRICITY",
  "UTILITY_SEWER", "UTILITY_HEAT", "UTILITY_DRAINAGE", "UTILITY_COMMUNICATION",
  "POWER_LINE_OVERHEAD",
  "EXISTING_TREE", "EXISTING_SHRUB", "GREEN_AREA", "PARKING",
  "ACCESS_ROUTE", "FENCE", "LIGHTING", "RETAINING_WALL", "UNDERGROUND_PARKING",
  "ANNOTATION", "UNKNOWN",
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
  UTILITY_COMMUNICATION: "#c084fc", POWER_LINE_OVERHEAD: "#ef4444", EXISTING_TREE: "#4ade80", EXISTING_SHRUB: "#86efac",
  GREEN_AREA: "#166534", PARKING: "#475569", ACCESS_ROUTE: "#818cf8",
  FENCE: "#9ca3af", LIGHTING: "#fde047", RETAINING_WALL: "#a16207", UNDERGROUND_PARKING: "#0e7490",
  ANNOTATION: "#556070", UNKNOWN: "#6b7280",
};

export interface Capabilities {
  dwgConverter: { available: boolean; kind: string | null; path: string | null; fallback?: string | null };
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
  /** package: set by the xref-package source file (e.g. the work boundary) */
  source: "rule" | "geometry" | "llm" | "user" | "package";
  requiresReview: boolean;
}

export interface LayerInfo {
  name: string;
  entityCount: number;
  color: string;
  classification: LayerClassification;
}

export interface EntityRender {
  color?: string;
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

/** One drawing of an AutoCAD eTransmit package (base general plan or an xref). */
export interface PackageSource {
  name: string;
  /** base | tp | up | pp | kl | brd | work_boundary | curbs | covers | other */
  kind: string;
  title: string;
  file: string;
  prefix: string;
  status: "merged" | "missing" | "failed" | "empty" | "unused";
  entitiesIn: number;
  entitiesKept: number;
  detail: string;
}

export interface PackageInfo {
  baseFile: string;
  clip: { mode: "work_boundary" | "work_boundary_layer" | "none"; source?: string; marginM?: number; boundaryAreaM2?: number };
  sources: PackageSource[];
}

export interface ProjectDocument {
  projectId: string;
  fileName: string;
  /** PACKAGE: a zip of a base drawing with its external references, merged */
  format: "DXF" | "DWG" | "PACKAGE";
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
  /** Present when the upload was an xref package (zip). */
  package?: PackageInfo | null;
}

export interface ChunkInfo {
  chunkId: string;
  bbox: [number, number, number, number]; // minx,miny,maxx,maxy
  summary: Record<string, number>;
  textLabels: string[];
}

// ---------------------------------------------------------------------------
// Regulatory verdict separation (iteration 5) — docs/API_CONTRACT.md, FROZEN.
// The regulatory verdict is DETERMINISTIC and fully separate from the LLM
// judge decision (which now only means "further optimization useful?").
// ---------------------------------------------------------------------------

export type RegulatoryStatus = "COMPLIANT" | "NON_COMPLIANT" | "NEEDS_REVIEW";

/** New uppercase per-check results (old stored data may still hold
 *  lowercase "passed" / "failed" / "n/a" — use normalizeCheckResult). */
export type CheckResult = "PASS" | "FAIL" | "UNVERIFIED" | "NOT_APPLICABLE";

export interface RegulatoryVerdict {
  status: RegulatoryStatus;
  /** e.g. "SITE_BOUNDARY_UNKNOWN", "UNVERIFIED_HARD_RULES:19" */
  blockers: string[];
  coverage: {
    /** verified applicable hard checks / applicable hard checks, 0..1 */
    regulatoryCoverage: number;
    parser: {
      siteBoundaryDetected: boolean;
      utilitiesTypedShare: number | null;
      roadsClassified: boolean;
      existingVegetationDetected: boolean;
      crossingsDetected: number;
    };
  };
  checkCounts: { pass: number; fail: number; unverified: number; notApplicable: number };
}

/** Defensive: maps both the new uppercase and the legacy lowercase
 *  per-check result values onto the frozen 4-status model. */
export function normalizeCheckResult(r: string | null | undefined): CheckResult {
  switch (r) {
    case "PASS": case "passed": return "PASS";
    case "FAIL": case "failed": return "FAIL";
    case "UNVERIFIED": return "UNVERIFIED";
    default: return "NOT_APPLICABLE"; // "NOT_APPLICABLE", legacy "n/a", unknown
  }
}

/** Defensive parser: old iterations / audits have no `regulatory` block at
 *  all, and partially-migrated records may miss nested fields. Returns null
 *  when there is no usable verdict (UI then shows "не рассчитан"). */
export function parseRegulatoryVerdict(v: unknown): RegulatoryVerdict | null {
  if (v == null || typeof v !== "object") return null;
  const o = v as Record<string, unknown>;
  const status = o.status;
  if (status !== "COMPLIANT" && status !== "NON_COMPLIANT" && status !== "NEEDS_REVIEW") return null;
  const cov = (o.coverage ?? {}) as Record<string, unknown>;
  const parser = (cov.parser ?? {}) as Record<string, unknown>;
  const counts = (o.checkCounts ?? {}) as Record<string, unknown>;
  const num = (x: unknown, fallback = 0) => (typeof x === "number" && Number.isFinite(x) ? x : fallback);
  return {
    status,
    blockers: Array.isArray(o.blockers) ? o.blockers.filter((b): b is string => typeof b === "string") : [],
    coverage: {
      regulatoryCoverage: Math.max(0, Math.min(1, num(cov.regulatoryCoverage))),
      parser: {
        siteBoundaryDetected: parser.siteBoundaryDetected === true,
        utilitiesTypedShare: typeof parser.utilitiesTypedShare === "number" ? parser.utilitiesTypedShare : null,
        roadsClassified: parser.roadsClassified === true,
        existingVegetationDetected: parser.existingVegetationDetected === true,
        crossingsDetected: num(parser.crossingsDetected),
      },
    },
    checkCounts: {
      pass: num(counts.pass),
      fail: num(counts.fail),
      unverified: num(counts.unverified),
      notApplicable: num(counts.notApplicable),
    },
  };
}

export type RuleRole = "regulatory" | "safe_buffer" | "design_heuristic";

export interface RegulatoryRule {
  id: string;
  enabled: boolean;
  /** speciesGenus: rule applies only to these Latin genera (e.g. 623-ПП
   *  п. 4.2.8 — heat mains); absent/null for kind-wide rules. */
  appliesTo: { plantingType: PlantKind[]; obstacleType: Category[]; speciesGenus?: string[] | null };
  constraint: {
    type: "min_distance" | "min_spacing" | "inside" | "species_blacklist" | "custom_geometry";
    distanceM: number | null;
    /** custom_geometry (visibility triangle): [short, long] legs in meters. */
    legsM?: number[] | null;
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
  /** Further acts that set the same requirement (e.g. 743-ПП табл. 3.6.1
   *  repeats СП 42 табл. 9.1). Absent on old payloads. */
  alsoCited?: CitedSource[];
  status: "verified" | "missing_source_data";
  severity: "hard" | "recommended";
  assumption: {
    assumedDistanceM: number | null;
    enabled: boolean;
    note: string;
  } | null;
  effectiveDistanceM: number | null;
  effectiveSource: "verified" | "assumption" | "inactive";
  /** Iteration 5: what kind of rule this is. Absent on old payloads;
   *  treat missing as "regulatory". safe_buffer / design_heuristic are
   *  NOT legal norms and are shown with a muted role badge. */
  role?: RuleRole;
  /** Iteration 5: applicability condition, e.g. {crownDiameterMaxM: 5}. */
  condition?: {
    crownDiameterMaxM?: number | null;
    unsignalizedCrossing?: boolean | null;
    speedLimitKmhMax?: number | null;
  } | null;
}

/** "same_value": the act repeats the value; "reference": the act requires
 *  the norm by reference to another table. */
export type CitationRelation = "same_value" | "reference";

export interface CitedSource {
  document: string;
  clause: string | null;
  url?: string | null;
  excerpt?: string | null;
  relation?: CitationRelation | null;
}

export interface SpeciesInfo {
  id: string;
  kind: PlantKind;
  displayName: string;
  displayNameRu: string;
  latin?: string;
  /** deciduous | evergreen — winter renders drop deciduous leaves */
  foliage?: string | null;
  parameters: { crownRadiusM: number; matureHeightM: number; rootZoneRadiusM: number };
  /** Illustrative nursery-stock defaults (rules/species.yaml planting_stock). */
  plantingStock?: {
    status?: string; ageYears?: number; heightM?: number[] | number; trunkGirthCm?: number[];
    rootBallM?: number[]; crownRadiusM?: number;
  } | null;
  /** Illustrative growth defaults for the visual age slider. */
  growth?: { status?: string; yearsToMature?: number } | null;
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
  /** binds only its rule's planting kinds; not cut from the allowed area */
  perKind?: boolean;
}

export type AllowedBasis = "site_boundary" | "cover_lawns" | "vegetation_evidence" | "extents_fallback";

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
  /** Iteration 5: deterministic regulatory verdict. Absent on old records —
   *  the UI shows "статус не рассчитан" then. Parse with parseRegulatoryVerdict. */
  regulatory?: RegulatoryVerdict | null;
}

export interface Placement {
  id: string;
  kind: PlantKind;
  speciesId: string;
  position: [number, number];
  crownRadiusM: number;
  score: number;
  distances: Record<string, number | null>;
  /** Iteration 6: true after POST /placements/{id}/move. */
  manuallyMoved?: boolean;
}

/** Iteration 5: structured design-heuristic warning. Old stored reports may
 *  still contain plain strings — the union below handles both. */
export interface ValidationWarning {
  plantingId: string;
  ruleId: string;
  actual: number;
  requiredM: number;
  kind: "design_heuristic";
}

export interface ValidationReport {
  valid: boolean;
  hardViolations: { plantingId: string; ruleId: string; actual: number; requiredM: number; obstacle: string }[];
  warnings: (string | ValidationWarning)[];
  metrics: { plantingCount: number; validCount: number; coverageScore: number; distributionScore: number };
}

/** Why rejected candidates may not be planted (ТЗ п. 8). `key` is a rule id
 *  (norm in the registry) or a geometric reason: OUTSIDE_SITE,
 *  NOT_PLANTING_AREA, ON_OBJECT:<category>, NARROW_STRIP. */
export interface RejectionReason {
  key: string;
  /** points for which this is the primary reason */
  count: number;
  byKind: Record<string, number>;
  /** points for which it is any of the reasons */
  involved: number;
}

export interface Rejections {
  total: number;
  byReason: RejectionReason[];
  samples: {
    position: [number, number];
    speciesId: string;
    kind: PlantKind;
    primary: string;
    reasons: string[];
    actualM?: number;
    requiredM?: number;
  }[];
}

export type IterationDetail = IterationSummary & {
  placements: Placement[];
  rejectedCandidates: [number, number][];
  candidateCount: number;
  validation: ValidationReport;
  /** absent on iterations computed before reasons were recorded */
  rejections?: Rejections;
  /** recommended lawn (ТЗ §2 п. 3); absent on older iterations */
  lawn?: LawnInfo;
};

export interface LawnInfo {
  areaM2: number;
  polygonCount: number;
  polygons: PolygonWithHoles[];
  explanation: string;
  /** rule id in the registry — its source is the norm (743-ПП п. 3.8) */
  ruleId: string;
  basis: string;
  minWidthM: number;
}

export interface ExplanationCheck {
  ruleId: string;
  /** New data: uppercase CheckResult; OLD stored data: lowercase legacy
   *  values. Always render via normalizeCheckResult. */
  result: CheckResult | "passed" | "failed" | "n/a";
  actualM: number | null;
  requiredM: number | null;
  comparator: ">=" | "outside";
  obstacleCategory: string;
  effectiveSource: "verified" | "assumption";
  source: {
    document: string;
    clause: string | null;
    title: string;
    status: string;
    alsoCited?: Pick<CitedSource, "document" | "clause" | "relation">[];
  };
  /** Why a check is not PASS (Russian, from the backend), e.g. a large crown. */
  reason?: string | null;
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
// Manual placement editing (iteration 6) — docs/API_CONTRACT.md.
// ---------------------------------------------------------------------------

export type LiveCheckVerdict = "OK" | "UNVERIFIED" | "VIOLATIONS";

/** Same shape as ExplanationCheck plus the rule's role. */
export interface LiveCheck extends ExplanationCheck {
  role?: RuleRole | null;
}

export interface LiveSpacingCheck {
  ruleId: string;
  nearestM: number | null;
  requiredM: number;
  role?: RuleRole | null;
  result: "PASS" | "WARNING" | "FAIL";
}

/** GET /placements/check — stateless live check for drag preview. */
export interface PlacementLiveCheck {
  position: [number, number];
  verdict: LiveCheckVerdict;
  insideAllowed: boolean;
  inVisibilityTriangle: boolean;
  spacing: LiveSpacingCheck | null;
  checks: LiveCheck[];
}

/** POST /placements/{id}/move — persists and revalidates the iteration. */
export interface MovePlacementResponse {
  placement: Placement;
  validation: {
    valid: boolean;
    hardViolations: ValidationReport["hardViolations"];
    warnings: ValidationReport["warnings"];
  };
  /** Parse with parseRegulatoryVerdict (defensive). */
  regulatory?: unknown;
  metrics?: IterationSummary["metrics"];
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
  format: "DWG" | "DXF" | "PACKAGE" | null;
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
  /** Iteration 5 columns; absent on old cached audits. */
  unverified?: number;
  notApplicable?: number;
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
  /** Iteration 5: deterministic regulatory verdict for the audited iteration.
   *  Absent on old audits. Parse with parseRegulatoryVerdict. */
  regulatory?: RegulatoryVerdict | null;
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
