# API Contract — Green Placement MVP

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
  appliesTo: { plantingType: PlantKind[]; obstacleType: Category[] };
  constraint: { type: "min_distance" | "min_spacing" | "inside" | "species_blacklist"; distanceM: number | null; units: "m" };
  source: { document: string; documentDate: string | null; clause: string | null; title: string; url: string | null; excerpt: string | null };
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
