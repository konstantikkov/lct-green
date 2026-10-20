import { useMemo, useRef, useState, useEffect } from "react";
import { ALL_CATEGORIES, CATEGORY_COLORS, type Category, type LayerInfo } from "../../api/types";
import { isDebug } from "../../debug";
import { overrideLayerCategory, setLayersVisibility, toggleLayerVisibility } from "../../state/actions";
import { useApp } from "../../state/store";
import { Badge, Dropdown, Section } from "../ui";
import { CLASS_SOURCE_LABELS, categoryLabel, layerDisplayName, plural } from "../../labels";
import type { PackageInfo } from "../../api/types";

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
  const shown = layerDisplayName(layer.name);

  return (
    <div className="group flex items-center gap-1.5 py-[3px]">
      <input
        type="checkbox"
        checked={visible}
        onChange={() => toggleLayerVisibility(layer.name)}
        className="h-3 w-3 shrink-0 accent-emerald-600"
        title="Показать/скрыть слой"
      />
      <span
        className="h-2.5 w-2.5 shrink-0 rounded-[2px] border border-black/20 dark:border-black/40"
        style={{ background: CATEGORY_COLORS[c.category] ?? layer.color }}
      />
      <span className="min-w-0 flex-1 truncate text-xs text-ink-200" title={layer.name}>
        {shown.source && <span className="text-ink-500">{shown.source} · </span>}
        {shown.layer}
      </span>
      <span className="shrink-0 font-mono text-2xs text-ink-500">{layer.entityCount}</span>
      {c.requiresReview && (
        <span className="shrink-0 text-2xs text-amber-600 dark:text-amber-400" title="Классификация требует проверки">⚠</span>
      )}
      <Dropdown
        open={menuOpen}
        onOpenChange={setMenuOpen}
        trigger={
          <Badge
            tone={c.requiresReview ? "amber" : "grey"}
            title={`класс определён ${CLASS_SOURCE_LABELS[c.source] ?? c.source} — нажмите, чтобы изменить категорию`}
            onClick={() => setMenuOpen(!menuOpen)}
          >
            {categoryLabel(c.category)} {confidence}%
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
            {categoryLabel(cat)}
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
          ⚠ {reviewCount} на проверку
        </span>
      ) : (
        <span className="shrink-0 text-2xs text-emerald-600 dark:text-emerald-400">✓</span>
      )}
      <span className="shrink-0 text-2xs text-ink-500">({ruLayers(layers.length)})</span>
    </div>
  );
}

// --- Panel -------------------------------------------------------------------

const PACKAGE_STATUS: Record<string, string> = {
  missing: "нет в пакете",
  failed: "не прочитан",
  empty: "пустой",
  unused: "не использован",
};

/** What the xref package consisted of and what made it into the plan. */
function PackageSummary({ pkg }: { pkg: PackageInfo }) {
  const [open, setOpen] = useState(false);
  const unused = pkg.sources.filter((s) => s.status === "unused");
  const refs = pkg.sources.filter((s) => s.kind !== "base" && s.status !== "unused");
  const merged = refs.filter((s) => s.status === "merged");
  const problems = refs.filter((s) => s.status !== "merged");
  const byTitle = new Map<string, number>();
  for (const s of merged) byTitle.set(s.title, (byTitle.get(s.title) ?? 0) + 1);
  const base = pkg.baseFile.split("/").pop();
  return (
    <div className="mt-2 border-t border-ink-700 pt-1.5 text-2xs leading-relaxed text-ink-400" data-testid="package-summary">
      <div className="mb-0.5 uppercase tracking-wider text-ink-500">Пакет с внешними ссылками</div>
      <div>
        Основной чертёж: <span className="text-ink-200">{base}</span>. Подключено {merged.length} из {refs.length}{" "}
        {plural(refs.length, "ссылки", "ссылок", "ссылок")}: {[...byTitle].map(([t, n]) => (n > 1 ? `${t} ×${n}` : t)).join(", ")}.
      </div>
      {(pkg.clip.mode === "work_boundary" || pkg.clip.mode === "work_boundary_layer") && (
        <div>Чертёж обрезан по границам работ с запасом {pkg.clip.marginM} м.</div>
      )}
      {problems.length > 0 && (
        <div
          className="text-amber-700 dark:text-amber-400"
          title={problems.map((s) => `${s.name}: ${s.detail}`).join("\n")}
        >
          Не подключены: {problems.map((s) => `${s.name} (${PACKAGE_STATUS[s.status] ?? s.status})`).join(", ")}.
        </div>
      )}
      {unused.length > 0 && (
        <div className="text-amber-700 dark:text-amber-400" title={unused.map((s) => s.file).join("\n")}>
          Не использованы — отдельные чертежи, на которые основной не ссылается (другой объект; загрузите
          каждый отдельно): {unused.map((s) => s.name).join(", ")}.
        </div>
      )}
      <button className="mt-0.5 underline decoration-dotted hover:text-ink-200" onClick={() => setOpen(!open)}>
        {open ? "Скрыть состав" : "Показать состав"}
      </button>
      {open && (
        <div className="mt-1 max-h-48 overflow-y-auto">
          {pkg.sources.map((s) => (
            <div key={s.name} className="flex gap-1.5">
              <span className="w-28 shrink-0 text-ink-500">{s.title}</span>
              <span className="min-w-0 flex-1 truncate" title={s.file || s.detail}>{s.name}</span>
              <span className="shrink-0 font-mono">
                {s.status === "merged" ? `${s.entitiesKept}/${s.entitiesIn}` : PACKAGE_STATUS[s.status] ?? s.status}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

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
    <Section title="Интерпретация">
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
            Проверьте слои перед генерацией: {reviewLayers.length}
          </span>
          <span className="font-semibold underline decoration-dotted">Проверить</span>
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
            Показать все слои чертежа ({doc.layers.length})
          </button>
        </div>
      ) : (
        <div>
          <div className="mb-1 flex items-center justify-between">
            <button
              className="text-2xs text-ink-400 underline decoration-dotted hover:text-ink-200"
              onClick={() => { setShowRaw(false); setReviewOnly(false); }}
            >
              ← К сводке
            </button>
            {reviewOnly && (
              <button
                className="text-2xs text-ink-400 underline decoration-dotted hover:text-ink-200"
                onClick={() => setReviewOnly(false)}
              >
                Показать все слои
              </button>
            )}
          </div>
          <div className="max-h-64 overflow-y-auto pr-1">
            {rawLayers.map((l) => (
              <LayerRow key={l.name} layer={l} />
            ))}
            {rawLayers.length === 0 && (
              <div className="py-1 text-2xs text-ink-500">слоёв на проверку нет</div>
            )}
          </div>
        </div>
      )}

      {doc.package && <PackageSummary pkg={doc.package} />}

      {isDebug && doc.conversionLog && doc.conversionLog.length > 0 && (
        <div className="mt-2 border-t border-ink-700 pt-1.5">
          <div className="mb-1 text-2xs uppercase tracking-wider text-ink-500">Журнал разбора (отладка)</div>
          <div className="max-h-32 overflow-y-auto font-mono text-2xs leading-relaxed text-ink-500">
            {doc.conversionLog.map((line, i) => <div key={i}>{line}</div>)}
          </div>
        </div>
      )}
    </Section>
  );
}
