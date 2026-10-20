import { useCallback, useEffect, useMemo, useState } from "react";
import { Btn } from "./ui";
import { PanoramaViewer, panoramaConditions, SEASON_LABELS, type Panorama, type Season } from "../viewer/PanoramaViewer";
import type { SceneAppearance } from "./RenderWorkflow";
import type { PanoramaMarker } from "../viewer/SceneManager";

export type PanoramaTools = {
  viewpoint: () => { x: number; y: number; heading: number } | null;
  pick: (picker: ((point: [number, number] | null) => void) | null) => void;
  markers: (points: PanoramaMarker[], onClick: ((id: string) => void) | null) => void;
};

const QUALITY: Record<string, string> = { preview: "Черновик · 2K", high: "Высокое · 4K", ultra: "Максимум · 8K" };
const pending = (p: Panorama) => p.state === "QUEUED" || p.state === "RENDERING";
const minutes = (s: number) => (s < 60 ? "меньше минуты" : `~${Math.ceil(s / 60)} мин`);

/** 360° Cycles panoramas: an automatic walkable tour, plus manual points. */
export function PanoramaPanel({ projectId, iterationId, reconstruction, tools, disabled, appearance, onAppearanceChange }: {
  appearance:SceneAppearance;onAppearanceChange:(settings:SceneAppearance)=>void;
  projectId: string; iterationId?: string; disabled: boolean;
  reconstruction: { buildingHeightM: number; showFences: boolean }; tools: PanoramaTools;
}) {
  const [items, setItems] = useState<Panorama[]>([]);
  const [point, setPoint] = useState<{ x: number; y: number; heading: number } | null>(null);
  const [picking, setPicking] = useState(false);
  const [eyeHeight, setEyeHeight] = useState(1.6), [quality, setQuality] = useState("preview"), [grass, setGrass] = useState(true);
  const [count, setCount] = useState(6);
  // "" = mature (as checked and exported)
  const age=appearance.ageYears===null?'':String(appearance.ageYears),season=appearance.season;
  const setAge=(value:string)=>onAppearanceChange({...appearance,ageYears:value===''?null:Number(value)});
  const setSeason=(value:Season)=>onAppearanceChange({...appearance,season:value});
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
    tools.markers([
      ...items.filter(i => i.state !== "FAILED").map(i => ({ id: i.id, x: i.x, y: i.y, state: (i.state === "COMPLETED" ? "done" : "pending") as PanoramaMarker["state"] })),
      ...(point ? [{ ...point, state: "active" as const }] : []),
    ], setViewing);
  }, [items, point, tools]);
  useEffect(() => () => { tools.pick(null); tools.markers([], null); }, [tools]);

  async function post(path: string, body: object) {
    setStarting(true); setStatus("Подготовка сцены…");
    try {
      const r = await fetch(`/api/projects/${projectId}/${path}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...body, eyeHeight, quality, grass, iterationId, reconstruction,
          ageYears: age === "" ? null : Number(age), season }),
      });
      const d = await r.json();
      if (!r.ok) throw Error(d.detail?.message || "Не удалось запустить рендер");
      setItems(list => [...d, ...list]); setStatus("");
      return true;
    } catch (e) { setStatus(e instanceof Error ? e.message : String(e)); return false; }
    finally { setStarting(false); }
  }
  async function startTour() { await post("panorama-tour", { maxPoints: count }); }
  async function startPoint() {
    const submitted = point;
    if (!submitted) return;
    // A point picked while the request was in flight is kept for the next render.
    if (await post("panoramas", submitted)) setPoint(p => (p === submitted ? null : p));
  }
  function pickPoint() {
    if (picking) { tools.pick(null); setPicking(false); return; }
    setPicking(true); setStatus("Кликните по земле в 3D-сцене");
    tools.pick(p => {
      setPicking(false); setStatus("");
      if (p) setPoint({ x: p[0], y: p[1], heading: tools.viewpoint()?.heading ?? 0 });
    });
  }
  function centrePoint() {
    const v = tools.viewpoint();
    if (v) { setPoint(v); setStatus(""); } else setStatus("Откройте 3D-редактор");
  }
  async function remove(jobId: string) {
    const r = await fetch(`/api/projects/${projectId}/panoramas/${jobId}`, { method: "DELETE" });
    if (r.ok || r.status === 404) setItems(list => list.filter(i => i.jobId !== jobId));
    else setStatus("Дождитесь окончания рендера, затем удалите");
  }

  const jobs = useMemo(() => {
    const groups = new Map<string, Panorama[]>();
    for (const item of items) groups.set(item.jobId, [...(groups.get(item.jobId) ?? []), item]);
    return [...groups.values()];
  }, [items]);
  const done = items.filter(i => i.state === "COMPLETED");
  const active = jobs.find(g => g.some(pending));
  const activeDone = active?.filter(i => i.state === "COMPLETED").length ?? 0;
  const remaining = active?.find(i => i.state === "RENDERING")?.remainingSeconds;
  const activePercent = active ? Math.round(active.reduce((sum, i) => sum + (i.state === "COMPLETED" ? 100 : i.progressPercent ?? 0), 0) / active.length) : 0;
  const select = "mt-1 block w-full rounded border border-ink-600 bg-ink-800 p-2";

  return <details className="mt-4 rounded border border-ink-600 p-3 text-xs" open>
    <summary className="cursor-pointer font-semibold">360° панорамы · прогулка по участку</summary>
    <p className="my-2 text-ink-400">Панорамный тур — офлайн-рендер: на CPU несколько часов на тур, в высоком качестве значительно дольше.</p>
    <fieldset disabled={disabled || starting} className="my-2 grid grid-cols-2 gap-3 disabled:opacity-60">
      <label>Точек в туре<select aria-label="Количество точек" value={count} onChange={e => setCount(Number(e.target.value))} className={select}>
        {[6, 12, 20].map(n => <option key={n} value={n}>{n}</option>)}</select></label>
      <label>Качество<select aria-label="Качество панорамы" value={quality} onChange={e => setQuality(e.target.value)} className={select}>
        {Object.entries(QUALITY).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label>Высота глаз<select aria-label="Высота глаз" value={eyeHeight} onChange={e => setEyeHeight(Number(e.target.value))} className={select}>
        <option value={1.6}>1,6 м · пешеход</option><option value={5}>5 м · 2-й этаж</option><option value={15}>15 м · 5-й этаж</option><option value={40}>40 м · дрон</option></select></label>
      <label className="flex items-end gap-2 pb-2"><input type="checkbox" checked={grass && season !== "winter"} disabled={season === "winter"} onChange={e => setGrass(e.target.checked)} />Объёмная трава</label>
      <label title="Условная модель роста (rules/species.yaml). План, проверка норм и DXF не меняются">Возраст посадок<select aria-label="Возраст посадок" value={age} onChange={e => setAge(e.target.value)} className={select}>
        <option value="">Зрелые · как в проверке</option><option value="0">При посадке</option><option value="5">Через 5 лет</option><option value="15">Через 15 лет</option><option value="30">Через 30 лет</option></select></label>
      <label title="Материалы и свет; геометрия сцены не меняется">Сезон<select aria-label="Сезон" value={season} onChange={e => setSeason(e.target.value as Season)} className={select}>
        {(Object.keys(SEASON_LABELS) as Season[]).map(k => <option key={k} value={k}>{SEASON_LABELS[k][0].toUpperCase() + SEASON_LABELS[k].slice(1)}</option>)}</select></label>
    </fieldset>
    <p className="mb-2 text-2xs text-ink-400">Сезон и возраст общие с 3D-видом и фото. Возраст меняет высоту и ширину крон по породе; сезон — листву, газон и свет. Применяется при новом запуске; готовые изображения не меняются.</p>
    {season === "winter" && <p className="-mt-1 mb-2 text-2xs text-ink-400">Зимой листопадные породы без листьев, газоны под снегом, объёмная трава не создаётся.</p>}
    {age !== "" && <p className="-mt-1 mb-2 text-2xs text-ink-400">Возраст меняет только размер растений в кадре — по условной модели роста. Проверка норм и экспорт DXF всегда по зрелым размерам.</p>}
    <div className="flex flex-wrap gap-2">
      <Btn variant="primary" onClick={() => void startTour()} disabled={disabled || starting || !!active}>{starting ? "Запуск…" : "Создать тур автоматически"}</Btn>
      <Btn onClick={() => done[0] && setViewing((done.find(i => i.tour) ?? done[0]).id)} disabled={!done.length}>Открыть панорамы{done.length ? ` · ${done.length}` : ""}</Btn>
    </div>
    <p className="mt-1 text-2xs text-ink-400">С видеокартой NVIDIA — около минуты на точку в 4K; на CPU — от 15 минут. Первые точки можно смотреть, пока рендерятся остальные.</p>
    {active && <div className="mt-3" aria-live="polite">
      <div className="mb-1 flex justify-between gap-2"><span>{active.length > 1 ? `Тур: готово ${activeDone} из ${active.length}` : "Рендер точки"}</span><span>{remaining ? `осталось ${minutes(remaining)}` : active.some(i => i.state === "QUEUED" && !activeDone) ? "в очереди" : ""}</span></div>
      <progress aria-label="Прогресс панорам" className="h-2 w-full accent-emerald-500" max="100" value={Math.max(activePercent, 1)} />
    </div>}
    {status && <p className="mt-2" role="status">{status}</p>}

    <details className="mt-3 rounded border border-ink-700 p-2">
      <summary className="cursor-pointer">Добавить точку вручную</summary>
      <div className="my-2 flex flex-wrap gap-2">
        <Btn onClick={pickPoint} disabled={disabled}>{picking ? "Отменить выбор" : "Указать кликом"}</Btn>
        <Btn onClick={centrePoint} disabled={disabled || picking}>Центр экрана</Btn>
        <Btn onClick={() => void startPoint()} disabled={disabled || starting || !point}>Рендер точки</Btn>
      </div>
      {point ? <p className="font-mono text-ink-300">X {point.x.toFixed(1)} · Y {point.y.toFixed(1)} · взгляд {Math.round(point.heading)}°</p>
        : <p className="text-ink-400">Ручные точки связываются с точками тура в радиусе 40 м.</p>}
    </details>

    {jobs.length > 0 && <ul className="mt-3 space-y-2">
      {jobs.map(group => {
        const first = group[0], ready = group.filter(i => i.state === "COMPLETED");
        const failed = group.find(i => i.state === "FAILED");
        return <li key={first.jobId} className="flex items-center justify-between gap-2 rounded bg-ink-800 p-2">
          <span>{first.tour ? `Тур · ${group.length} точек` : `Точка ${first.x.toFixed(0)}, ${first.y.toFixed(0)}`} · {first.eyeHeight} м · {panoramaConditions(first)}
            <span className="block text-2xs text-ink-400">{failed ? <span className="text-red-400" title={failed.error}>Ошибка рендера</span> : group.some(pending) ? `готово ${ready.length} из ${group.length}` : QUALITY[first.quality]}</span></span>
          <span className="flex gap-1">
            {ready.length > 0 && <Btn onClick={() => setViewing(ready[0].id)}>Открыть</Btn>}
            {!group.some(pending) && <Btn variant="ghost" onClick={() => void remove(first.jobId)} title="Удалить">✕</Btn>}
          </span>
        </li>;
      })}
    </ul>}
    {done.length > 0 && <p className="mt-2 text-2xs text-ink-400">Синий маркер в 3D-сцене — готовая панорама: кликните, чтобы открыть.</p>}
    {viewing && done.some(i => i.id === viewing) && <PanoramaViewer items={items.filter(i => i.state !== "FAILED")} startId={viewing} onClose={() => setViewing(null)} />}
  </details>;
}
