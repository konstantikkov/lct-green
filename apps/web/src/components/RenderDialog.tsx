import { useEffect, useState } from "react";
import { Btn } from "./ui";

export function RenderDialog({ projectId }: { projectId: string }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<{ statusUrl: string; imageUrl: string } | null>(null);
  const [status, setStatus] = useState("");
  const [image, setImage] = useState<string | null>(null);
  const [elevation, setElevation] = useState(23);
  const [azimuth, setAzimuth] = useState(225);
  const [distance, setDistance] = useState(.8);
  const [detail, setDetail] = useState("presentation");
  const [view, setView] = useState("aerial");
  const [quality, setQuality] = useState("preview");
  useEffect(() => {
    if (!job) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const r = await fetch(job.statusUrl);
        if (!r.ok) throw new Error(await r.text());
        const data = await r.json();
        if (stopped) return;
        if (data.state === "COMPLETED") { setImage(job.imageUrl); setBusy(false); setJob(null); setStatus("Готово"); return; }
        if (data.state === "FAILED") throw new Error(data.error);
        setStatus(data.state === "QUEUED" ? "Ожидание в очереди…" : "Рендер на CPU… Это может занять несколько минут.");
        timer = setTimeout(poll, 3000);
      } catch (e) { if (!stopped) { setStatus(String(e)); setBusy(false); setJob(null); } }
    };
    void poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, [job]);
  async function start() {
    setBusy(true); setStatus("Подготовка DXF и сцены…"); setImage(null);
    try {
      const r = await fetch(`/api/projects/${projectId}/renders`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ elevation, azimuth, distance, detail, view, ...(quality === "preview" ? {width:900,height:600,samples:32} : {width:1280,height:800,samples:96}) }),
      });
      if (!r.ok) throw new Error(await r.text());
      setJob(await r.json());
    } catch (e) { setStatus(String(e)); setBusy(false); }
  }
  return <>
    <Btn onClick={() => setOpen(true)}>Фоторендер</Btn>
    {open && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" role="dialog" aria-modal="true" aria-label="Локальный фоторендер">
      <div className="max-h-[90vh] w-full max-w-4xl overflow-auto rounded border border-ink-600 bg-ink-900 p-5 text-ink-100">
        <div className="flex justify-between gap-4"><h2 className="text-lg">Фоторендер · Cycles</h2><Btn onClick={() => setOpen(false)}>Закрыть</Btn></div>
        <p className="my-3 text-sm text-ink-300">Посадки — по выбранному плану. Фасады, клумбы, мощение и мебель в презентационном режиме — иллюстративные; в DXF и нормативную проверку они не добавляются.</p>
        <div className="my-3 flex flex-wrap gap-4 text-sm">
          <label>Детализация<select className="block rounded bg-ink-800 p-2" value={detail} onChange={e => setDetail(e.target.value)}><option value="presentation">Презентация: архитектура и ландшафт</option><option value="cad">CAD и посадки</option></select></label>
          <label>Качество<select className="block rounded bg-ink-800 p-2" value={quality} onChange={e => setQuality(e.target.value)}><option value="preview">Просмотр · 900×600</option><option value="final">Презентация · 1280×800</option></select></label>
          <label>Камера<select className="block rounded bg-ink-800 p-2" value={view} onChange={e => setView(e.target.value)}><option value="aerial">Вид сверху</option><option value="pedestrian">С уровня человека</option></select></label>
          <label>Направление {azimuth}°<input className="block" type="range" min="0" max="360" value={azimuth} onChange={e => setAzimuth(+e.target.value)} /></label>
          <label>Высота камеры {elevation}°<input className="block" type="range" min="5" max="85" value={elevation} onChange={e => setElevation(+e.target.value)} /></label>
          <label>Дистанция {distance.toFixed(1)}<input className="block" type="range" min=".2" max="2" step=".1" value={distance} onChange={e => setDistance(+e.target.value)} /></label>
        </div>
        <div className="flex flex-wrap gap-3">
          <Btn disabled={busy} onClick={() => void start()}>{busy ? "Рендер выполняется…" : "Создать изображение"}</Btn>
          <Btn onClick={() => window.open(`/api/projects/${projectId}/export/render-bundle`, "_blank")}>Скачать сцену для Blender</Btn>
          {image && <a href={image} target="_blank" rel="noreferrer" className="p-2 underline">Скачать PNG</a>}
        </div>
        <details className="my-3 text-sm text-ink-300"><summary className="cursor-pointer">Пример детализации: жилой двор</summary><p className="my-2">Иллюстративный проект для демонстрации материалов, архитектуры и благоустройства.</p><a href="/example/courtyard-reference.png" target="_blank" rel="noreferrer"><img src="/example/courtyard-reference.png" alt="Демонстрационный двор с архитектурой, мощением и многоярусным озеленением" className="mt-2 w-full rounded" loading="lazy" /></a></details>
        <p className="my-3 text-sm" role="status">{status}</p>
        {image && <img src={image} alt="Визуализация выбранного плана озеленения" className="w-full rounded" />}
      </div>
    </div>}
  </>;
}
