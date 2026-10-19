import { useCallback, useEffect, useState } from "react";
import { Btn } from "./ui";
import { PanoramaViewer, type Panorama } from "../viewer/PanoramaViewer";

export type PanoramaTools = {
  viewpoint: () => { x: number; y: number; heading: number } | null;
  pick: (picker: ((point: [number, number] | null) => void) | null) => void;
  markers: (points: { x: number; y: number; active?: boolean }[]) => void;
};

const QUALITY: Record<string, string> = { preview: "Черновик · 2048×1024", high: "Высокое · 4096×2048", ultra: "Максимум · 8192×4096" };
const pending = (p: Panorama) => p.state === "QUEUED" || p.state === "RENDERING";

/** 360° Cycles panoramas from standing points chosen in the 3D scene. */
export function PanoramaPanel({ projectId, iterationId, reconstruction, tools, disabled }: {
  projectId: string; iterationId?: string; disabled: boolean;
  reconstruction: { buildingHeightM: number; showFences: boolean }; tools: PanoramaTools;
}) {
  const [items, setItems] = useState<Panorama[]>([]);
  const [point, setPoint] = useState<{ x: number; y: number; heading: number } | null>(null);
  const [picking, setPicking] = useState(false);
  const [eyeHeight, setEyeHeight] = useState(1.6), [quality, setQuality] = useState("high"), [grass, setGrass] = useState(true);
  const [status, setStatus] = useState(""), [starting, setStarting] = useState(false);
  const [viewing, setViewing] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const r = await fetch(`/api/projects/${projectId}/panoramas`);
    if (r.ok) setItems(await r.json());
  }, [projectId]);
  useEffect(() => { void refresh().catch(() => {}); }, [refresh]);
  const busy = items.some(pending);
  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(() => void refresh().catch(() => {}), 3000);
    return () => clearInterval(timer);
  }, [busy, refresh]);

  useEffect(() => {
    tools.markers([...items.filter(i => i.state !== "FAILED").map(i => ({ x: i.x, y: i.y })), ...(point ? [{ ...point, active: true }] : [])]);
  }, [items, point, tools]);
  useEffect(() => () => { tools.pick(null); tools.markers([]); }, [tools]);

  function pickPoint() {
    if (picking) { tools.pick(null); setPicking(false); return; }
    setPicking(true); setStatus("Кликните по земле в 3D-сцене");
    tools.pick(p => {
      setPicking(false);
      if (!p) { setStatus(""); return; }
      setPoint({ x: p[0], y: p[1], heading: tools.viewpoint()?.heading ?? 0 }); setStatus("");
    });
  }
  function centrePoint() {
    const v = tools.viewpoint();
    if (v) { setPoint(v); setStatus(""); } else setStatus("Откройте 3D-редактор");
  }
  async function start() {
    const submitted = point;
    if (!submitted) return;
    setStarting(true); setStatus("Подготовка сцены…");
    try {
      const r = await fetch(`/api/projects/${projectId}/panoramas`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...submitted, eyeHeight, quality, grass, iterationId, reconstruction }),
      });
      const d = await r.json();
      if (!r.ok) throw Error(d.detail?.message || "Не удалось запустить рендер");
      // A point picked while the request was in flight is kept for the next render.
      setItems(list => [d, ...list]); setPoint(p => p === submitted ? null : p); setStatus("");
    } catch (e) { setStatus(e instanceof Error ? e.message : String(e)); }
    finally { setStarting(false); }
  }

  const done = items.filter(i => i.state === "COMPLETED");
  return <details className="mt-4 rounded border border-ink-600 p-3 text-xs" open>
    <summary className="cursor-pointer font-semibold">360° панорамы · Cycles</summary>
    <p className="my-2 text-ink-400">Трассировка лучей по геометрии CAD с теми же деревьями и материалами. Трава на газонах моделируется рядом с точкой съёмки. Результат одинаков между точками: можно переходить от одной панорамы к другой.</p>
    <div className="my-2 flex flex-wrap gap-2">
      <Btn onClick={pickPoint} disabled={disabled}>{picking ? "Отменить выбор" : "Указать точку кликом"}</Btn>
      <Btn onClick={centrePoint} disabled={disabled || picking}>Центр экрана</Btn>
    </div>
    {point && <p className="my-1 font-mono text-ink-300">X {point.x.toFixed(1)} · Y {point.y.toFixed(1)} · взгляд {Math.round(point.heading)}°</p>}
    <fieldset disabled={disabled || starting} className="my-2 grid grid-cols-2 gap-3 disabled:opacity-60">
      <label>Высота глаз<select aria-label="Высота глаз" value={eyeHeight} onChange={e => setEyeHeight(Number(e.target.value))} className="mt-1 block w-full rounded border border-ink-600 bg-ink-800 p-2">
        <option value={1.6}>1,6 м · пешеход</option><option value={5}>5 м · 2-й этаж</option><option value={15}>15 м · 5-й этаж</option><option value={40}>40 м · дрон</option></select></label>
      <label>Качество<select aria-label="Качество панорамы" value={quality} onChange={e => setQuality(e.target.value)} className="mt-1 block w-full rounded border border-ink-600 bg-ink-800 p-2">
        {Object.entries(QUALITY).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label className="col-span-2 flex items-start gap-2"><input type="checkbox" checked={grass} onChange={e => setGrass(e.target.checked)} />Объёмная трава на газонах в радиусе 30 м</label>
    </fieldset>
    <Btn variant="primary" onClick={() => void start()} disabled={disabled || starting || !point}>{starting ? "Запуск…" : "Рендер 360°"}</Btn>
    <p className="mt-1 text-2xs text-ink-400">С видеокартой NVIDIA — около минуты на 4K; на CPU — от 15 минут.</p>
    {status && <p className="mt-2" role="status">{status}</p>}
    {items.length > 0 && <ul className="mt-3 space-y-2">
      {items.map(item => <li key={item.jobId} className="flex items-center justify-between gap-2 rounded bg-ink-800 p-2">
        <span className="font-mono">{item.x.toFixed(1)}, {item.y.toFixed(1)} · {item.eyeHeight} м</span>
        {item.state === "COMPLETED" ? <Btn onClick={() => setViewing(item.jobId)}>Открыть 360°</Btn>
          : item.state === "FAILED" ? <span className="text-red-400" title={item.error}>Ошибка</span>
          : <span className="text-ink-300">{item.state === "QUEUED" ? "В очереди" : `${item.progressPercent ?? 1}%`}{item.remainingSeconds ? ` · ${Math.ceil(item.remainingSeconds / 60)} мин` : ""}</span>}
      </li>)}
    </ul>}
    {viewing && done.length > 0 && <PanoramaViewer items={done} startId={viewing} onClose={() => setViewing(null)} />}
  </details>;
}
