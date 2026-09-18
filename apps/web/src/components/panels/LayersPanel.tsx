import { useState } from "react";
import { ALL_CATEGORIES, CATEGORY_COLORS, type LayerInfo } from "../../api/types";
import { overrideLayerCategory, setOverlay, toggleLayerVisibility } from "../../state/actions";
import { useApp, type OverlayState } from "../../state/store";
import { Badge, Checkbox, Dropdown, Section } from "../ui";

const OVERLAYS: { key: keyof OverlayState; label: string }[] = [
  { key: "originalCad", label: "Original CAD" },
  { key: "exclusionZones", label: "Exclusion zones" },
  { key: "allowedArea", label: "Allowed area" },
  { key: "rejectedCandidates", label: "Rejected candidates" },
  { key: "trees", label: "Generated trees" },
  { key: "shrubs", label: "Generated shrubs" },
  { key: "existingVegetation", label: "Existing vegetation" },
  { key: "chunkGrid", label: "Chunk grid" },
];

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

export function LayersPanel() {
  const doc = useApp((s) => s.document);
  const overlays = useApp((s) => s.overlays);
  if (!doc) return null;

  return (
    <Section title={`Layers (${doc.layers.length})`}>
      <div className="max-h-64 overflow-y-auto pr-1">
        {doc.layers.map((l) => (
          <LayerRow key={l.name} layer={l} />
        ))}
      </div>
      <div className="mt-2 border-t border-ink-700 pt-2">
        <div className="mb-1 text-2xs uppercase tracking-wider text-ink-500">Overlays</div>
        <div className="grid grid-cols-1 gap-1">
          {OVERLAYS.map(({ key, label }) => (
            <Checkbox
              key={key}
              checked={overlays[key]}
              onChange={(v) => setOverlay(key, v)}
              label={label}
            />
          ))}
        </div>
      </div>
    </Section>
  );
}
