import { useMemo, useRef, useState, useEffect } from "react";
import { ALL_CATEGORIES, CATEGORY_COLORS, type Category, type LayerInfo } from "../../api/types";
import { isDebug } from "../../debug";
import { overrideLayerCategory, setLayersVisibility, toggleLayerVisibility } from "../../state/actions";
import { useApp } from "../../state/store";
import { Badge, Dropdown, Section } from "../ui";

// --- Semantic grouping ------------------------------------------------------

interface GroupDef {
  id: string;
  label: string;
  color: string;
  match: (c: Category) => boolean;
}

const GROUP_DEFS: GroupDef[] = [
  { id: "buildings", label: "Здания", color: CATEGORY_COLORS.BUILDING, match: (c) => c === "BUILDING" },
  {
    id: "roads", label: "Дороги", color: CATEGORY_COLORS.ROAD,
    match: (c) => c === "ROAD" || c === "CURB" || c === "SIDEWALK" || c === "PARKING" || c === "ACCESS_ROUTE",
  },
  { id: "utilities", label: "Сети", color: CATEGORY_COLORS.UTILITY, match: (c) => c.startsWith("UTILITY") },
  {
    id: "vegetation", label: "Существующие деревья", color: CATEGORY_COLORS.EXISTING_TREE,
    match: (c) => c === "EXISTING_TREE" || c === "EXISTING_SHRUB" || c === "GREEN_AREA",
  },
  {
    id: "boundaries", label: "Границы", color: CATEGORY_COLORS.SITE_BOUNDARY,
    match: (c) => c === "SITE_BOUNDARY" || c === "FENCE",
  },
  { id: "other", label: "Прочее / аннотации", color: CATEGORY_COLORS.ANNOTATION, match: () => true },
];

function groupLayers(layers: LayerInfo[]): Map<string, LayerInfo[]> {
  const map = new Map<string, LayerInfo[]>(GROUP_DEFS.map((g) => [g.id, []]));
  for (const l of layers) {
    const def = GROUP_DEFS.find((g) => g.match(l.classification.category))!;
    map.get(def.id)!.push(l);
  }
  return map;
}

function ruLayers(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return `${n} слой`;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return `${n} слоя`;
  return `${n} слоёв`;
}

// --- Raw layer row (unchanged behavior: visibility + override dropdown) -----

function LayerRow({ layer }: { layer: LayerInfo }) {
  const visible = useApp((s) => s.layerVisibility[layer.name] !== false);
  const [menuOpen, setMenuOpen] = useState(false);
  const c = layer.classification;
  const confidence = Math.round(c.confidence * 100);

  return (
    <div className="group flex items-center gap-1.5 py-[3px]">
      <input
        type="checkbox"
        checked={visible}
        onChange={() => toggleLayerVisibility(layer.name)}
        className="h-3 w-3 shrink-0 accent-emerald-600"
        title="Toggle visibility"
      />
      <span
        className="h-2.5 w-2.5 shrink-0 rounded-[2px] border border-black/20 dark:border-black/40"
        style={{ background: CATEGORY_COLORS[c.category] ?? layer.color }}
      />
      <span className="min-w-0 flex-1 truncate text-xs text-ink-200" title={layer.name}>
        {layer.name}
      </span>
      <span className="shrink-0 font-mono text-2xs text-ink-500">{layer.entityCount}</span>
      {c.requiresReview && (
        <span className="shrink-0 text-2xs text-amber-600 dark:text-amber-400" title="Classification requires review">⚠</span>
      )}
      <Dropdown
        open={menuOpen}
        onOpenChange={setMenuOpen}
        trigger={
          <Badge
            tone={c.requiresReview ? "amber" : "grey"}
            title={`source: ${c.source} — click to override`}
            onClick={() => setMenuOpen(!menuOpen)}
          >
            {c.category} {confidence}%
          </Badge>
        }
      >
        {ALL_CATEGORIES.map((cat) => (
          <button
            key={cat}
            className={`flex w-full items-center gap-2 px-2 py-1 text-left text-2xs hover:bg-ink-700 ${
              cat === c.category ? "text-emerald-700 dark:text-emerald-300" : "text-ink-200"
            }`}
            onClick={() => {
              setMenuOpen(false);
              if (cat !== c.category) void overrideLayerCategory(layer.name, cat);
            }}
          >
            <span className="h-2 w-2 rounded-[2px]" style={{ background: CATEGORY_COLORS[cat] }} />
            {cat}
          </button>
        ))}
      </Dropdown>
    </div>
  );
}

// --- Semantic group row ------------------------------------------------------

function GroupRow({ def, layers }: { def: GroupDef; layers: LayerInfo[] }) {
  const layerVisibility = useApp((s) => s.layerVisibility);
  const boxRef = useRef<HTMLInputElement>(null);

  const visibleCount = layers.filter((l) => layerVisibility[l.name] !== false).length;
  const allVisible = visibleCount === layers.length;
  const reviewCount = layers.filter((l) => l.classification.requiresReview).length;

  useEffect(() => {
    if (boxRef.current) boxRef.current.indeterminate = visibleCount > 0 && !allVisible;
  }, [visibleCount, allVisible]);

  return (
    <div className="flex items-center gap-1.5 py-1">
      <input
        ref={boxRef}
        type="checkbox"
        checked={allVisible}
        onChange={() => setLayersVisibility(layers.map((l) => l.name), !allVisible)}
        className="h-3 w-3 shrink-0 accent-emerald-600"
        title={`Показать/скрыть: ${def.label}`}
      />
      <span
        className="h-2.5 w-2.5 shrink-0 rounded-[2px] border border-black/20 dark:border-black/40"
        style={{ background: def.color }}
      />
      <span className="min-w-0 flex-1 truncate text-xs text-ink-200">{def.label}</span>
      {reviewCount > 0 ? (
        <span className="shrink-0 text-2xs text-amber-600 dark:text-amber-400" title="Есть неуверенно распознанные слои">
          ⚠ {reviewCount} uncertain
        </span>
      ) : (
        <span className="shrink-0 text-2xs text-emerald-600 dark:text-emerald-400">✓</span>
      )}
      <span className="shrink-0 text-2xs text-ink-500">({ruLayers(layers.length)})</span>
    </div>
  );
}

// --- Panel -------------------------------------------------------------------

export function LayersPanel() {
  const doc = useApp((s) => s.document);
  const [showRaw, setShowRaw] = useState(false);
  const [reviewOnly, setReviewOnly] = useState(false);

  const groups = useMemo(
    () => (doc ? groupLayers(doc.layers) : new Map<string, LayerInfo[]>()),
    [doc?.layers], // eslint-disable-line react-hooks/exhaustive-deps
  );
  if (!doc) return null;

  const reviewLayers = doc.layers.filter((l) => l.classification.requiresReview);
  const rawLayers = reviewOnly ? reviewLayers : doc.layers;

  return (
    <Section title="Interpretation">
      {/* One interrupt chip instead of permanent warning noise. */}
      {reviewLayers.length > 0 && (
        <button
          data-testid="review-chip"
          className="mb-2 flex w-full items-center gap-1.5 rounded-sm border border-amber-300 bg-amber-50 px-2 py-1.5 text-left text-2xs text-amber-800 hover:bg-amber-100 dark:border-amber-700 dark:bg-amber-950/50 dark:text-amber-300 dark:hover:bg-amber-900/50"
          onClick={() => { setShowRaw(true); setReviewOnly(true); }}
          title="Слои с неуверенной классификацией — проверьте перед генерацией"
        >
          <span>⚠</span>
          <span className="flex-1">
            {reviewLayers.length} layer{reviewLayers.length > 1 ? "s" : ""} require review before generation
          </span>
          <span className="font-semibold underline decoration-dotted">Review</span>
        </button>
      )}

      {!showRaw ? (
        <div data-testid="layer-summary">
          {GROUP_DEFS.map((def) => {
            const layers = groups.get(def.id) ?? [];
            if (layers.length === 0) return null;
            return <GroupRow key={def.id} def={def} layers={layers} />;
          })}
          <button
            className="mt-1.5 text-2xs text-ink-400 underline decoration-dotted hover:text-ink-200"
            onClick={() => { setShowRaw(true); setReviewOnly(false); }}
          >
            Show {doc.layers.length} CAD layers
          </button>
        </div>
      ) : (
        <div>
          <div className="mb-1 flex items-center justify-between">
            <button
              className="text-2xs text-ink-400 underline decoration-dotted hover:text-ink-200"
              onClick={() => { setShowRaw(false); setReviewOnly(false); }}
            >
              ← Back to summary
            </button>
            {reviewOnly && (
              <button
                className="text-2xs text-ink-400 underline decoration-dotted hover:text-ink-200"
                onClick={() => setReviewOnly(false)}
              >
                Show all layers
              </button>
            )}
          </div>
          <div className="max-h-64 overflow-y-auto pr-1">
            {rawLayers.map((l) => (
              <LayerRow key={l.name} layer={l} />
            ))}
            {rawLayers.length === 0 && (
              <div className="py-1 text-2xs text-ink-500">no layers require review</div>
            )}
          </div>
        </div>
      )}

      {isDebug && doc.conversionLog && doc.conversionLog.length > 0 && (
        <div className="mt-2 border-t border-ink-700 pt-1.5">
          <div className="mb-1 text-2xs uppercase tracking-wider text-ink-500">Parser log (debug)</div>
          <div className="max-h-32 overflow-y-auto font-mono text-2xs leading-relaxed text-ink-500">
            {doc.conversionLog.map((line, i) => <div key={i}>{line}</div>)}
          </div>
        </div>
      )}
    </Section>
  );
}
