import {createPortal} from "react-dom";
import {useEffect,useState} from "react";
import {Btn} from "./ui";
export type PhotoCapture={image:string;depth:string;semantic:string;buildingCount:number;camera:{position:number[];target:number[];fov:number;aspect:number}};
type Job={statusUrl:string;imageUrl:string;sourceUrl:string;manifestUrl:string};
/** Generate from an immutable color/depth capture of the user's current camera. */
export function PhotoDialog({projectId,capture}:{projectId:string;capture:()=>PhotoCapture}) {
  const storageKey=`greenplan-photo:${projectId}`;
  const [saved]=useState<{job:Job;completed:boolean}|null>(()=>{try{const d=JSON.parse(sessionStorage.getItem(storageKey)||"null");return d?.job?.statusUrl?.startsWith(`/api/projects/${projectId}/photos/`)?d:null;}catch{return null;}});
  const [open,setOpen]=useState(false),[input,setInput]=useState<PhotoCapture|null>(null);
  const [job,setJob]=useState<Job|null>(saved&&!saved.completed?saved.job:null),[result,setResult]=useState<Job|null>(saved?.completed?saved.job:null);
  const [status,setStatus]=useState(""),[busy,setBusy]=useState(!!saved&&!saved.completed);
  const [lighting,setLighting]=useState("daylight"),[strength,setStrength]=useState(.85),[seed,setSeed]=useState(42);
  const [previousPhoto,setPreviousPhoto]=useState<Job|null>(null);
  const [available,setAvailable]=useState(false),[modelStatus,setModelStatus]=useState("Проверка локальной модели…");
  useEffect(()=>{try{const current=job||result;if(current)sessionStorage.setItem(storageKey,JSON.stringify({job:current,completed:!!result}));else sessionStorage.removeItem(storageKey);}catch{/* storage may be disabled */}},[job,result,storageKey]);
  function captureView(){try{setInput(capture());setResult(null);setStatus("");}catch(e){setStatus(String(e));}}
  useEffect(()=>{
    if(!open)return;let stopped=false;
    void fetch('/api/photo-capabilities').then(async r=>{if(!r.ok)throw Error('Модель недоступна');return r.json();}).then(d=>{if(!stopped){setAvailable(d.available);setModelStatus(d.message);}}).catch(()=>{if(!stopped)setModelStatus("Не удалось проверить локальную модель");});
    void fetch(`/api/projects/${projectId}/photos`).then(r=>r.ok?r.json():[]).then(rows=>{if(!stopped)setPreviousPhoto(rows.find((r:Job&{state:string})=>r.state==='COMPLETED')||null);}).catch(()=>{});
    return()=>{stopped=true;};
  },[open,projectId]);
  useEffect(()=>{
    if(!job)return;let stopped=false;let timer:ReturnType<typeof setTimeout>;
    const poll=async()=>{try{
      const r=await fetch(job.statusUrl);if(!r.ok)throw Error('Не удалось получить статус');const d=await r.json();if(stopped)return;
      if(d.state==='COMPLETED'){setResult(job);setJob(null);setBusy(false);setStatus("Фото готово");return;}
      if(d.state==='FAILED')throw Error(d.error);
      setStatus(d.state==='QUEUED'?"В очереди локальной модели…":`${d.message||"Генерация на этом сервере…"}${d.step?` · шаг ${d.step}${d.totalSteps?` / ${d.totalSteps}`:""}`:""}`);
      timer=setTimeout(poll,3000);
    }catch(e){if(!stopped){setStatus(String(e));setJob(null);setBusy(false);}}};void poll();
    return()=>{stopped=true;clearTimeout(timer);};
  },[job]);
  async function generate(){
    if(!input)return;setBusy(true);setResult(null);setStatus("Передача выбранного кадра и глубины локальной модели…");
    try{const r=await fetch(`/api/projects/${projectId}/photos`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...input,lighting,strength,seed,resolution:512})});const d=await r.json();if(!r.ok)throw Error(d.detail?.message||"Не удалось запустить генерацию");setJob(d);}
    catch(e){setStatus(String(e));setBusy(false);}
  }
  return <>
    <Btn onClick={()=>{setOpen(true);if(!busy&&!result)captureView();}} title="Создать фотографию локальной AI-моделью с текущей камеры">AI-фото</Btn>
    {open&&createPortal(<div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-3" role="dialog" aria-modal="true" aria-label="Фотография из CAD-макета">
      <div className="max-h-[94vh] w-full max-w-6xl overflow-auto rounded border border-ink-600 bg-ink-900 p-4 text-ink-100">
        <div className="flex items-center justify-between gap-3"><h2 className="text-lg font-semibold">Макет → фотография</h2><Btn onClick={()=>setOpen(false)}>Закрыть</Btn></div>
        <p className="my-2 text-sm text-ink-300">Камера и композиция взяты из текущего 3D-макета. Локальная модель добавляет фотографические материалы, растительность, свет и небо; карта типов объектов направляет форму и вид растительности.</p>
        <div className="my-3 grid gap-3 md:grid-cols-2">
          <div><div className="mb-1 text-xs text-ink-400">Исходный кадр · выбранная камера</div>{(input||job||result)&&<img src={input?.image||(job||result)?.sourceUrl} alt="CAD-макет с выбранной камеры" className="w-full rounded bg-ink-800"/>}</div>
          <div><div className="mb-1 text-xs text-ink-400">Локальная генерация · Realistic Vision + ControlNet Segmentation</div>{result?<img src={result.imageUrl} alt="Сгенерированная фотографическая визуализация" className="w-full rounded"/>:<div className="flex min-h-48 items-center justify-center rounded border border-dashed border-ink-600 p-6 text-center text-sm text-ink-400">Выберите свет и создайте фото. CPU-генерация может занять несколько минут.</div>}</div>
        </div>
        <div className="my-3 flex flex-wrap items-end gap-4 text-sm">
          <label>Свет<select className="block rounded bg-ink-800 p-2" value={lighting} disabled={busy} onChange={e=>setLighting(e.target.value)}><option value="daylight">Естественный дневной</option><option value="evening">Тёплый вечер</option><option value="overcast">Мягкий пасмурный</option></select></label>
          <label>Детализация фото {strength.toFixed(2)}<input aria-label="Детализация фото" className="block" type="range" min=".55" max=".95" step=".05" disabled={busy} value={strength} onChange={e=>setStrength(+e.target.value)}/><span className="text-2xs text-ink-400">Меньше — ближе к макету; больше — свободнее детали</span></label>
          <label>Вариант<input aria-label="Вариант фото" className="block w-24 rounded bg-ink-800 p-2" type="number" min="0" max="2147483647" value={seed} disabled={busy} onChange={e=>setSeed(Math.max(0,Math.min(2147483647,Math.floor(+e.target.value))))}/></label>
          <Btn onClick={()=>void generate()} disabled={busy||!input||!available}>{busy?"Генерация…":"Создать фото"}</Btn>
          <Btn disabled={busy} onClick={captureView}>Обновить кадр</Btn>
          {previousPhoto&&<Btn disabled={busy} onClick={()=>{setInput(null);setResult(previousPhoto);setStatus("Сохранённое фото · исходная камера показана слева");}}>Последнее фото</Btn>}
          {result&&<><a href={result.imageUrl} download className="p-2 underline">Скачать фото</a><a href={result.manifestUrl} target="_blank" rel="noreferrer" className="p-2 underline">Камера и параметры</a></>}
        </div>
        <p className="text-xs text-ink-400">{modelStatus} · обработка локально · CPU: до 512 px по длинной стороне</p>
        <p role="status" className="my-2 text-sm">{status}</p>
        <p className="text-xs text-ink-400">AI-визуализация может менять мелкие детали. Проверяйте её по исходному кадру; точные границы и посадки остаются в CAD. Фотографии-примеры задают качество света и материалов, а не планировку.</p>
      </div>
    </div>,document.body)}
  </>;
}
