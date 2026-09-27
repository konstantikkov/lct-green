# API Contract — Green Placement MVP

## Regulatory verdict separation (iteration 5) — FROZEN

Per-check result: `"PASS" | "FAIL" | "UNVERIFIED" | "NOT_APPLICABLE"`.
- PASS: geometry satisfied against a rule whose value is VERIFIED from a source.
- FAIL: geometry violates any enforced rule (verified or assumed — safe direction).
- UNVERIFIED: geometry satisfied but the rule value is an ASSUMED demo value
  (`missing_source_data`), or an applicability condition can't be confirmed
  (e.g. crown diameter > 5 m for СП 42 табл. 9.1; site boundary inferred from
  extents; generic untyped-utility safe buffer; recommended-range rules).
- NOT_APPLICABLE: no such obstacle in the drawing / rule not applicable.

Overall `regulatory.status` (DETERMINISTIC, judge can never change it):
any FAIL → `"NON_COMPLIANT"`; else any applicable-hard UNVERIFIED or any
blocker → `"NEEDS_REVIEW"`; else `"COMPLIANT"`.

`regulatory` object (in iteration records, /compliance, export report):
```ts
interface RegulatoryVerdict {
  status: "COMPLIANT" | "NON_COMPLIANT" | "NEEDS_REVIEW";
  blockers: string[];            // e.g. "SITE_BOUNDARY_UNKNOWN", "UNVERIFIED_HARD_RULES:19"
  coverage: {                    // deterministic, 0..1
    regulatoryCoverage: number;  // verified applicable hard checks / applicable hard checks
    parser: { siteBoundaryDetected: boolean; utilitiesTypedShare: number|null;
              roadsClassified: boolean; existingVegetationDetected: boolean;
              crossingsDetected: number };
  };
  checkCounts: { pass: number; fail: number; unverified: number; notApplicable: number };
}
```
Judge changes: decision means ONLY "further optimization iteration useful?".
Judge confidence is capped in code at `0.3 + 0.7*regulatoryCoverage`. Judge
output is sanity-passed: parameterChanges with no-op reasons dropped; if any
assessment dimension != GOOD while issues==[] a deterministic issue is added.
UI labels: regulatory status shown separately from the judge decision chip
("Оптимизация: завершена/можно улучшить" vs "Соответствие: ТРЕБУЕТ ПРОВЕРКИ").
Rule model gains `role: "regulatory" | "safe_buffer" | "design_heuristic"` and
optional `condition: {crownDiameterMaxM?: number}`; design_heuristic violations
are warnings, never hardViolations.

Registry v0.3.0 (743-ПП / 623-ПП verification) adds, all optional:
* `alsoCited: CitedSource[]` — further acts that set the same requirement
  (743-ПП п. 3.6.3 табл. 3.6.1 repeats СП 42 табл. 9.1, `relation:
  "same_value"`; 623-ПП п. 4.2.4 requires it by reference to табл. 7.6
  МГСН 1.01-99, `relation: "reference"`). Explanation checks carry it as
  `source.alsoCited: {document, clause, relation}[]`.
* `appliesTo.speciesGenus: string[]` — species-specific rule (623-ПП п. 4.2.8,
  heat mains): enforced only for placements whose catalog species belongs to
  one of these Latin genera; an unknown species is not exempt. Such rules do
  not produce exclusion zones (zones are per planting kind) — the candidate
  filter, validator and explanation apply them per species.
* `constraint.legsM: [short, long]` and `condition: {unsignalizedCrossing,
  speedLimitKmhMax}` on VISIBILITY_TRIANGLE_NO_TALL_PLANTS (623-ПП п. 9.4.2,
  8 x 40 м at 40 km/h). The norm is verified; its applicability is not
  readable from the drawing, so the check stays UNVERIFIED, never PASS.



## Admin dashboard & compliance audit (iteration 3)

### `GET /api/projects/summary` → rich listing for the start screen
```ts
interface ProjectSummary {
  id: string; name: string; fileName: string; createdAt: number; state: string;
  format: "DWG"|"DXF"|null; units: string|null;
  entityCount: number|null; layerCount: number|null; uncertainLayers: number|null;
  result: { iterationCount: number; treeCount: number|null; shrubCount: number|null;
            hardViolations: number; coverageScore: number|null;
            distributionScore: number|null; judgeDecision: string|null } | null;
  exports: { dxf: string; report: string } | null;  // href download paths
}
```
(older projects may have null meta — render dashes)

### `GET /api/projects/{id}/compliance` → per-tree law-compliance audit
Deterministic re-check of EVERY placement of the selected iteration against every
effective rule (YAML registry + ACTIVE document-derived REG_* rules). 409 NO_RESULT
when no generation exists.
```ts
interface ComplianceAudit {
  iterationId: string; rulesetVersion: string;
  documentDerivedRules: number; verifiedRules: number; assumedRules: number;
  summary: { placements: number; compliant: number; withViolations: number; checksTotal: number };
  byDocument: Record<string, { checks: number; passed: number; failed: number }>;
  byRule: Record<string, { checks: number; passed: number; failed: number;
                           document: string; effectiveSource: string; requiredM: number|null }>;
  placements: { id: string; kind: string; speciesId: string; position: [number, number];
                verdict: "COMPLIANT"|"VIOLATIONS"; failingRules: string[];
                checks: ExplanationCheck[] }[];  // same check shape as /explanation
}
```

## Iteration 4 additions

- `DELETE /api/projects/{id}` → `{deleted: id}`; 404 PROJECT_NOT_FOUND. Removes
  rows, blobs, disk cache and uploaded files.
- RunStatus now includes `progress: number` (0–100, coarse per-iteration; 100 on
  COMPLETED) for progress bars.
- `POST /api/regulations/text` — body `{text: string, title?: string}`. Creates a
  text "document" (fileName `<title>.txt`), fragments per paragraph, runs LLM
  extraction SYNCHRONOUSLY and returns `{document: RegDocument, candidates:
  RuleCandidate[]}` so the UI can show candidates for review immediately.
  Errors: 422 EMPTY_TEXT, 413 TEXT_TOO_LARGE, 409 LLM_UNAVAILABLE,
  502 EXTRACTION_FAILED. Candidates then follow the normal review flow
  (verify/approve/edit/activate); activation invalidates cached project
  constraints server-side, so the next constraints fetch / generation uses them.

## Regulations feature (WS4) — /api/regulations

```ts
type DocStatus = "UPLOADED" | "PARSED" | "EXTRACTED" | "FAILED";
type RuleCandidateStatus = "EXTRACTED" | "NEEDS_REVIEW" | "VERIFIED" | "ACTIVE" | "DEPRECATED" | "REJECTED";

interface RegDocument {
  id: string; fileName: string; title: string | null; documentNumber: string | null;
  jurisdiction: string | null; publicationDate: string | null; effectiveDate: string | null;
  version: string; fileHash: string; status: DocStatus; uploadedAt: number;
  pageCount: number | null; fragmentCount: number; candidateCounts: Record<RuleCandidateStatus, number>;
  parseLog: string[];
}
interface Fragment { id: string; page: number | null; hierarchyPath: string; kind: "clause"|"table"|"note"|"heading"; text: string; order: number; }
interface RuleCandidate {
  id: string; documentId: string; ruleVersion: number;
  source: { documentId: string; documentVersion: string; page: number | null;
            hierarchyPath: string; fragmentIds: string[]; sourceText: string };
  applicability: { jurisdiction: string | null; plantType: string[]; objectTypes: string[]; conditions: string[] };
  constraint: { type: "MIN_DISTANCE"|"MIN_SPACING"|"OTHER"; subject: string; referenceObject: string;
                operator: string; value: number | null; unit: string | null };
  exceptions: string[]; references: string[];
  machineExecutable: boolean; confidence: number; status: RuleCandidateStatus;
  verification: { verdict: "SUPPORTED"|"PARTIALLY_SUPPORTED"|"UNSUPPORTED"|"AMBIGUOUS"|null;
                  numericLiteralFound: boolean; notes: string | null;
                  model: string | null; promptVersion: string | null } | null;
  reviewer: string | null; reviewDate: number | null;
  extraction: { model: string; promptVersion: string };
}
```

- `POST /api/regulations/documents` — multipart `file` (.pdf/.docx). Stores original,
  parses hierarchy synchronously, returns RegDocument. 422 {code:"UNSUPPORTED_FORMAT"|"PARSE_FAILED"}.
- `GET /api/regulations/documents` → RegDocument[]
- `GET /api/regulations/documents/{id}` → RegDocument
- `GET /api/regulations/documents/{id}/fragments?path=prefix` → Fragment[] (paged by ?offset/limit, default 200)
- `POST /api/regulations/documents/{id}/extract` → 202 {jobId}; extraction via local LLM
  produces candidates (status EXTRACTED / NEEDS_REVIEW). Poll document.candidateCounts.
  409 {code:"LLM_UNAVAILABLE"} when no local LLM reachable.
- `GET /api/regulations/candidates?documentId=&status=` → RuleCandidate[]
- `POST /api/regulations/candidates/{id}/verify` → runs second-pass verifier, returns updated candidate.
- `PATCH /api/regulations/candidates/{id}` — body {action:"approve"|"reject"|"edit", edits?:{constraint?, applicability?}, reviewer?:string}.
  approve: NEEDS_REVIEW/EXTRACTED/VERIFIED → VERIFIED (then ACTIVE via activate). Every edit bumps ruleVersion (old version retained server-side).
- `POST /api/regulations/candidates/{id}/activate` → status ACTIVE (only from VERIFIED; numericLiteralFound must be true for numeric rules).
- `POST /api/regulations/candidates/{id}/deactivate` → DEPRECATED.
- ACTIVE candidates are merged into GET /api/rules as RegulatoryRule entries with
  status:"verified", effectiveSource:"verified", full source provenance (document number,
  clause=hierarchyPath, excerpt=sourceText, url:null) and id `REG_<candidateId>`;
  the constraint engine consumes them automatically (no code change per rule).



Backend: FastAPI at `http://localhost:8000`. All JSON. All geometry coordinates are in **meters** (backend normalizes CAD units at ingestion). 2D plan coordinates `[x, y]`.

Frontend dev server proxies `/api/*` → `http://localhost:8000`.

## Shared enums

```ts
type Category =
  | "SITE_BOUNDARY" | "BUILDING" | "ROAD" | "CURB" | "SIDEWALK"
  | "UTILITY" | "UTILITY_WATER" | "UTILITY_GAS" | "UTILITY_ELECTRICITY"
  | "UTILITY_SEWER" | "UTILITY_HEAT" | "UTILITY_DRAINAGE" | "UTILITY_COMMUNICATION"
  | "EXISTING_TREE" | "EXISTING_SHRUB" | "GREEN_AREA" | "PARKING"
  | "ACCESS_ROUTE" | "FENCE" | "LIGHTING" | "ANNOTATION" | "UNKNOWN";

type PipelineState =
  | "UPLOADED" | "CONVERTING" | "PARSING" | "CLASSIFYING"
  | "BUILDING_CONSTRAINTS" | "GENERATING_CANDIDATES" | "OPTIMIZING"
  | "VALIDATING" | "JUDGING" | "RERUNNING" | "COMPLETED" | "FAILED";

type Strategy = "balanced" | "maximum_trees" | "maximum_canopy" | "street_greening";
type PlantKind = "TREE" | "SHRUB";
```

Suggested category colors (dark UI):

```ts
const CATEGORY_COLORS: Record<string, string> = {
  SITE_BOUNDARY: "#f59e0b", BUILDING: "#94a3b8", ROAD: "#64748b",
  CURB: "#7c8aa0", SIDEWALK: "#a8b3c4", UTILITY: "#e879f9",
  UTILITY_WATER: "#38bdf8", UTILITY_GAS: "#fbbf24", UTILITY_ELECTRICITY: "#f472b6",
  UTILITY_SEWER: "#a78bfa", UTILITY_HEAT: "#fb923c", UTILITY_DRAINAGE: "#22d3ee",
  UTILITY_COMMUNICATION: "#c084fc", EXISTING_TREE: "#4ade80", EXISTING_SHRUB: "#86efac",
  GREEN_AREA: "#166534", PARKING: "#475569", ACCESS_ROUTE: "#818cf8",
  FENCE: "#9ca3af", LIGHTING: "#fde047", ANNOTATION: "#556070", UNKNOWN: "#6b7280",
};
```

## Endpoints

### `GET /api/capabilities`
```json
{
  "dwgConverter": {"available": true, "kind": "libredwg", "path": "/usr/bin/dwg2dxf"},
  "localLlm": {"enabled": true, "baseUrl": "http://localhost:8080", "model": "qwen2.5", "reachable": false}
}
```

### `POST /api/projects` — multipart form, field `file` (.dxf or .dwg)
Creates project, converts (if DWG), parses, classifies synchronously.
Returns `ProjectDocument` (below). Error 422 with `{"detail": {"code": "DWG_CONVERTER_UNAVAILABLE", "message": "..."}}` when DWG uploaded without converter.

### `GET /api/projects` → `[{id, name, fileName, createdAt, state}]`

### `GET /api/projects/{id}/document` → `ProjectDocument`
```ts
interface ProjectDocument {
  projectId: string;
  fileName: string;
  format: "DXF" | "DWG";
  cadVersion: string;          // e.g. "AC1032"
  units: string;               // e.g. "mm", "m", "unitless(assumed mm)"
  unitsScaleToMeters: number;
  entityCount: number;
  layerCount: number;
  extents: { min: [number, number]; max: [number, number] };  // meters
  state: PipelineState;
  layers: LayerInfo[];
  entities: EntityRender[];    // simplified render geometry, meters
}
interface LayerInfo {
  name: string;
  entityCount: number;
  color: string;               // hex from DXF ACI or default
  classification: {
    category: Category;
    confidence: number;        // 0..1
    source: "rule" | "geometry" | "llm" | "user";
    requiresReview: boolean;
  };
}
interface EntityRender {
  id: string;                  // internal id
  sourceHandle: string | null;
  type: string;                // LINE, LWPOLYLINE, ...
  layer: string;
  kind: "polyline" | "polygon" | "point" | "text";
  points: number[];            // flat [x0,y0,x1,y1,...] meters (polyline/polygon)
  closed?: boolean;
  position?: [number, number]; // point/text
  text?: string;
  radius?: number;             // circles rendered as polygon but radius kept for points
}
```
Note: `entities` may be capped (~20000) for rendering; `entityCount` is the true count.

### `PATCH /api/projects/{id}/layers/{layerName}/classification`
Body `{"category": "UTILITY_WATER"}` → returns updated `LayerInfo`. (URL-encode layer name.)
Invalidates cached constraints.

### `GET /api/projects/{id}/chunks` → `[{chunkId, bbox: [minx,miny,maxx,maxy], summary: {…counts by category}, textLabels: string[]}]`

### `GET /api/rules` → `RegulatoryRule[]`
```ts
interface RegulatoryRule {
  id: string;                       // e.g. "TREE_BUILDING_MIN_DISTANCE"
  enabled: boolean;
  appliesTo: { plantingType: PlantKind[]; obstacleType: Category[]; speciesGenus: string[] | null };
  constraint: { type: "min_distance" | "min_spacing" | "inside" | "species_blacklist" | "custom_geometry"; distanceM: number | null; legsM?: number[] | null; units: "m" };
  source: { document: string; documentDate: string | null; clause: string | null; title: string; url: string | null; excerpt: string | null };
  alsoCited: { document: string; clause: string | null; url: string | null; excerpt: string | null; relation: "same_value" | "reference" | null }[];
  status: "verified" | "missing_source_data";
  severity: "hard" | "recommended";
  assumption: {                      // present when status = missing_source_data
    assumedDistanceM: number | null; // demo/configurable value, clearly NOT verified
    enabled: boolean;                // whether the assumption is applied
    note: string;
  } | null;
  effectiveDistanceM: number | null; // what the engine actually uses (null = rule inactive)
  effectiveSource: "verified" | "assumption" | "inactive";
}
```

### `PATCH /api/rules/{ruleId}` — body `{enabled?, assumption?: {assumedDistanceM?, enabled?}}` → updated rule.

### `GET /api/species` → `[{id, kind, displayName, displayNameRu, parameters: {crownRadiusM, matureHeightM, rootZoneRadiusM}, regulatoryStatus: "allowed" | "unknown"}]`

### `GET /api/projects/{id}/constraints` → computed on demand, cached
```ts
interface ConstraintsResponse {
  siteBoundary: number[][] | null;       // array of rings, each flat [x,y,...]
  siteBoundarySource: "detected" | "extents_fallback";
  // how the planting domain was derived:
  //   site_boundary        — closed site boundary found on a boundary layer
  //   vegetation_evidence  — topo map with no closed areas: domain = zones around
  //                          existing vegetation symbols (infill planting)
  //   extents_fallback     — nothing better found; review layer classification
  allowedBasis: "site_boundary" | "vegetation_evidence" | "extents_fallback";
  exclusionZones: ExclusionZone[];
  allowedArea: { polygons: PolygonWithHoles[]; areaM2: number };
  repairLog: string[];
  inactiveRules: {ruleId: string; reason: string}[]; // e.g. missing_source_data & assumption disabled
}
interface PolygonWithHoles { shell: number[]; holes: number[][]; }
interface ExclusionZone {
  id: string;
  ruleId: string;
  generatedFrom: string;      // entity/layer id
  sourceLayer: string;
  obstacleCategory: Category;
  distanceM: number;
  effectiveSource: "verified" | "assumption";
  polygon: PolygonWithHoles;  // may be merged per rule
  source: { document: string; clause: string | null; status: string };
}
```

### `POST /api/projects/{id}/generate`
```json
{
  "plantKinds": ["TREE", "SHRUB"],
  "speciesIds": ["tree_linden", "shrub_lilac"],
  "strategy": "balanced",
  "targetCount": null,
  "seed": 42,
  "maxIterations": 3,
  "useLlmJudge": true
}
```
→ `202 {"runId": "run_xxx"}`; pipeline is async.

### `GET /api/projects/{id}/runs/{runId}` → run status
```ts
interface RunStatus {
  runId: string; state: PipelineState;
  stages: { stage: PipelineState; status: "pending" | "active" | "done" | "error"; detail: string }[];
  currentIteration: number;
  error: string | null;
}
```
### `GET /api/projects/{id}/runs/{runId}/events` — SSE stream, each event `data:` = RunStatus JSON. Ends after COMPLETED/FAILED. Poll fallback: GET above every 700ms.

### `GET /api/projects/{id}/iterations` → `IterationSummary[]`
```ts
interface IterationSummary {
  iterationId: string; index: number; runId: string;
  selected: boolean;                 // is it the final chosen solution
  parameters: Record<string, number | string>;
  seed: number;
  metrics: { treeCount: number; shrubCount: number; validCount: number;
             coverageScore: number; distributionScore: number; canopyAreaM2: number;
             hardViolations: number };
  judge: JudgeReport | null;
  runtimeMs: number;
}
interface JudgeReport {
  mode: "llm" | "heuristic_fallback";  // heuristic when local LLM unreachable/disabled
  decision: "ACCEPT" | "RERUN";
  score: { overall: number; coverage: number; distribution: number; confidence: number };
  issues: string[];
  recommendedParameterChanges: Record<string, number | null>;
  reasoningSummary: string;
  llmModel: string | null;
}
```

### `GET /api/projects/{id}/iterations/{iterationId}` → full iteration
`IterationSummary & { placements: Placement[]; rejectedCandidates: [x,y][]; candidateCount: number; validation: ValidationReport }`
```ts
interface Placement {
  id: string; kind: PlantKind; speciesId: string;
  position: [number, number];
  crownRadiusM: number;
  score: number;
  distances: Record<string, number | null>; // building, road, sidewalk, utility, boundary, existing_tree, nearest_planting
}
interface ValidationReport {
  valid: boolean;
  hardViolations: { plantingId: string; ruleId: string; actual: number; requiredM: number; obstacle: string }[];
  warnings: string[];
  metrics: { plantingCount: number; validCount: number; coverageScore: number; distributionScore: number };
}
```

### `GET /api/projects/{id}/placements/{placementId}/explanation`
```ts
interface Explanation {
  plantingId: string; position: [number, number]; speciesId: string; iteration: number;
  summary: string;                       // deterministic template text
  llmNarrative: string | null;           // only if local LLM available
  checks: {
    ruleId: string; result: "passed" | "n/a";
    actualM: number | null; requiredM: number | null; comparator: ">=";
    obstacleCategory: string;
    effectiveSource: "verified" | "assumption";
    source: { document: string; clause: string | null; title: string; status: string };
  }[];
}
```

### `GET /api/projects/{id}/export/dxf` → file `original-name_greening.dxf`
### `GET /api/projects/{id}/export/report` → file `original-name_greening_report.json`

## Error shape
Non-2xx: `{"detail": {"code": "STRING_CODE", "message": "human readable"}}` or FastAPI default `{"detail": "..."}`. Handle both.

## JudgeReport v2

Replaces the v1 `JudgeReport` above for all new runs (schema `judge-report-2`).
The judge runs ONLY when the deterministic validator reports `valid == true`;
otherwise the pipeline repairs deterministically and stores `judge: null` with
`judgeSkipped: "hard_violations"` on the iteration. There is NO LLM-invented
overall score: the UI summary derives from `deterministicComponents`, which are
computed in code from validator metrics and spatial diagnostics.

```ts
type JudgeDecision = "ACCEPT" | "RERUN" | "MANUAL_REVIEW"; // MANUAL_REVIEW = abstention, terminal for the run
type DimensionStatus = "GOOD" | "IMPROVABLE" | "POOR";

interface AssessmentDimension {
  status: DimensionStatus;
  evidence: string[];   // evidence IDs from the JudgePacket (q_i_j, cluster_N, under_q_i_j, layer_N);
}                       // unknown IDs are stripped server-side

interface JudgeReportV2 {
  schemaVersion: "judge-report-2";
  mode: "llm" | "heuristic_fallback";   // heuristic when local LLM unreachable/disabled
  decision: JudgeDecision;
  confidence: number;                   // 0..1
  assessment: {
    coverage: AssessmentDimension;
    distribution: AssessmentDimension;
    clustering: AssessmentDimension;
    utilization: AssessmentDimension;
    robustnessToUncertainty: AssessmentDimension;
  };
  issues: { type: string; severity: "LOW" | "MEDIUM" | "HIGH";
            evidenceIds: string[]; description: string }[];
  parameterChanges: {                   // whitelist-enforced; clamped to [min,max] and
    parameter: string;                  //   to current ± maxDeltaPerIteration in code;
    currentValue: number;               //   informational only when decision == ACCEPT
    proposedValue: number;
    reason: string;
    evidenceIds: string[];
  }[];
  summary: string;
  deterministicComponents: {            // computed in code (judge_packet.py), never by the LLM
    coverage: number; distribution: number;
    utilizationBalance: number;         // 1 − underused-area share
    clusterPenalty: number;             // share of plantings inside dense clusters
  };
  // observability — recorded for every judgment
  promptVersion: string | null;         // prompts/judge/{version}.md; null in heuristic mode
  model: string | null;
  temperature: number | null;
  inputSchemaVersion: "judge-packet-1";
  judgeInputHash: string;               // sha256 of the canonical JudgePacket JSON
  // backward-compatible keys (v1 consumers)
  reasoningSummary: string;             // mirrors `summary`
  recommendedParameterChanges: Record<string, number>; // parameter → proposedValue
  llmModel: string | null;
}
```

Iteration-record additions alongside `judge`:

```ts
interface IterationJudgeExtras {
  judgeSkipped: "hard_violations" | null;   // judge not invoked (plan invalid after repair)
  deterministicRepair: { removed: number; passes: number } | null; // pre-judge repair, ≤ 3 passes
  needsManualReview: boolean;               // true when decision == MANUAL_REVIEW (terminal, no rerun)
}
```

Rerun retention rule (pipeline, deterministic): a judge-triggered rerun becomes
the selected iteration ONLY if it is deterministically valid AND the objective
(0.5·coverage + 0.5·distribution) improves by ≥ 0.02 AND neither component
drops by more than 0.05; otherwise the previous selection is kept.
