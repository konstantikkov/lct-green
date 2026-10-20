import {usePanelResize} from "./PanelResize";
import {useEffect,useRef,useState} from "react";
import {Btn} from "./ui";
import {useApp} from "../state/store";
import {loadIteration} from "../state/actions";
import {SEASON_LABELS,type Season} from "../viewer/PanoramaViewer";
import {PanoramaPanel,type PanoramaTools} from "./PanoramaPanel";
export type SceneAppearance={season:import("../viewer/PanoramaViewer").Season;ageYears:number|null};
export type PhotoCapture={image:string;depth:string;semantic:string;siteBounds:{min:[number,number];max:[number,number]};buildingCount:number;fenceCount?:number;camera:{position:number[];target:number[];fov:number;aspect:number}};
type Job={jobId?:string;statusUrl:string;imageUrl:string;sourceUrl:string;manifestUrl:string;iterationId?:string|null};
type Message={role:string;content:string};
type Progress={state:string;progressPercent?:number;remainingSeconds?:number;queuePosition?:number;message?:string};
function minutes(seconds:number){return seconds<60?"меньше минуты":`около ${Math.ceil(seconds/60)} мин`;}
export function RenderWorkflow({projectId,capture,camera,sceneReady,onAspectChange,onReconstructionChange,reconstructionSettings,panorama,appearance,onAppearanceChange}:{appearance:SceneAppearance;onAppearanceChange:(settings:SceneAppearance)=>void;panorama:PanoramaTools;reconstructionSettings:{buildingHeightM:number;showFences:boolean};onReconstructionChange:(settings:{buildingHeightM:number;showFences:boolean})=>void;projectId:string;sceneReady:boolean;onAspectChange:(aspect:number|null)=>void;capture:(aspect?:number,longEdge?:number)=>PhotoCapture;camera:(view:"aerial"|"garden")=>void}){
  const [tab,setTab]=useState<"tour"|"render">("tour");
  const [background,setBackground]=useState("park");
  const [buildingHeight,setBuildingHeight]=useState(reconstructionSettings.buildingHeightM),[showFences,setShowFences]=useState(reconstructionSettings.showFences);
  const resize=usePanelResize("greenplan-render-width",340,"left");
  const [ratio,setRatio]=useState("16:9"),[resolution,setResolution]=useState(1536),[highReady,setHighReady]=useState(false);
  const [lighting,setLighting]=useState("sunny"),[strength,setStrength]=useState(.85),[preset,setPreset]=useState("advertising");
  const aspect=ratio==="viewport"?undefined:Number(ratio.split(":")[0])/Number(ratio.split(":")[1]);
  useEffect(()=>onAspectChange(tab==="render"?aspect??null:null),[tab,aspect,onAspectChange]);
  const panel=useRef<HTMLElement>(null);
  const moving=useApp(s=>s.placementMovesPending);
  const plans=useApp(s=>s.iterations),plan=useApp(s=>s.activeIteration),loading=useApp(s=>s.iterationLoadingId);
  const key=`greenplan-photo:${projectId}`;
  const [saved]=useState<{job:Job;completed:boolean}|null>(()=>{try{const d=JSON.parse(sessionStorage.getItem(key)||"null");return d?.job?.statusUrl?.startsWith(`/api/projects/${projectId}/photos/`)?d:null;}catch{return null;}});
  const [job,setJob]=useState<Job|null>(saved&&!saved.completed?saved.job:null),[result,setResult]=useState<Job|null>(saved?.completed?saved.job:null);
  const [busy,setBusy]=useState(!!saved&&!saved.completed),[sending,setSending]=useState(false),[ready,setReady]=useState(false);
  const [status,setStatus]=useState(""),[progress,setProgress]=useState<Progress|null>(null),[deadline,setDeadline]=useState(0),[now,setNow]=useState(Date.now());
  const samePlan=!result?.iterationId||result.iterationId===plan?.iterationId;
  const [resultSize,setResultSize]=useState<{width:number;height:number}|null>(null);
  const [messages,setMessages]=useState<Message[]>([]),[text,setText]=useState("");
  useEffect(()=>{let stopped=false;
    void fetch('/api/photo-capabilities').then(r=>r.json()).then(d=>{if(!stopped){setReady(d.available);setHighReady(!!d.highResolutionAvailable);if(!d.highResolutionAvailable)setResolution(512);if(!d.available)setStatus(d.message);}}).catch(()=>setStatus("Не удалось подключиться к генерации"));
    if(!saved)void fetch(`/api/projects/${projectId}/photos`).then(r=>r.ok?r.json():[]).then(rows=>{if(stopped)return;const pending=rows.find((r:Job&{state:string})=>r.state==='GENERATING'||r.state==='QUEUED');const latest=rows.find((r:Job&{state:string})=>r.state==='COMPLETED');if(latest)setResult(latest);if(pending){setJob(pending);setBusy(true);}}).catch(()=>{});
    return()=>{stopped=true;};
  },[projectId,saved]);
  useEffect(()=>{try{const current=job||result;if(current)sessionStorage.setItem(key,JSON.stringify({job:current,completed:!job&&!!result}));}catch{}},[job,result,key]);
  useEffect(()=>{if(!busy)return;if(panel.current)panel.current.scrollTop=0;const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer);},[busy]);
  useEffect(()=>{if(!job)return;let stopped=false;let timer:ReturnType<typeof setTimeout>;
    const poll=async()=>{try{const r=await fetch(job.statusUrl);if(!r.ok)throw Error("Не удалось получить статус");const d=await r.json();if(stopped)return;
      setProgress(d);setDeadline(Date.now()+(d.remainingSeconds??420)*1000);setNow(Date.now());
      if(d.state==='COMPLETED'){setResult(job);setJob(null);setBusy(false);setStatus("Рендер готов");return;}
      if(d.state==='FAILED')throw Error(d.error);
      setStatus(d.state==='QUEUED'?`Ожидание в очереди${d.queuePosition?` · место ${d.queuePosition}`:""}`:d.message||"Генерируем выбранный ракурс…");timer=setTimeout(poll,3000);
    }catch(e){if(!stopped){setStatus(e instanceof Error?e.message:String(e));setJob(null);setBusy(false);try{sessionStorage.removeItem(key);}catch{}}}};void poll();return()=>{stopped=true;clearTimeout(timer);};
  },[job,key]);
  useEffect(()=>{setResultSize(null);if(!result)return;let stopped=false;void fetch(result.manifestUrl).then(r=>r.ok?r.json():null).then(d=>{if(!stopped&&d)setResultSize(d);}).catch(()=>{});void fetch(result.statusUrl+'/chat').then(r=>r.ok?r.json():[]).then(d=>{if(!stopped)setMessages(d);}).catch(()=>{});return()=>{stopped=true;};},[result]);
  async function generate(){if(!plan)return;setBusy(true);setStatus("Фиксируем план и камеру…");setProgress({state:'QUEUED',progressPercent:1});setDeadline(Date.now()+(resolution>=1024?1200000:420000));
    try{const input=capture(aspect,resolution>=1024?1280:768);const r=await fetch(`/api/projects/${projectId}/photos`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...input,iterationId:plan.iterationId,lighting,background,strength,seed:42,resolution,...appearance})});const d=await r.json();if(!r.ok)throw Error(d.detail?.message||"Не удалось запустить рендер");setJob(d);}
    catch(e){setBusy(false);setProgress(null);setStatus(e instanceof Error?e.message:String(e));}
  }
  async function refine(){if(!result||!samePlan||!text.trim())return;setSending(true);setStatus("LLM уточняет изменения…");
    try{const r=await fetch(result.statusUrl+'/refine',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text.trim()})});const d=await r.json();if(!r.ok)throw Error(d.detail?.message||"Не удалось отправить сообщение");setMessages(d.conversation);setText("");setStatus(d.reply);if(d.job){setJob(d.job);setBusy(true);setProgress({state:'QUEUED',progressPercent:1});setDeadline(Date.now()+420000);}}
    catch(e){setStatus(e instanceof Error?e.message:String(e));}finally{setSending(false);}
  }
  return <section ref={panel} style={{"--render-panel-width":`${resize.width}px`} as import("react").CSSProperties} className="relative shrink-0 max-h-[48%] overflow-y-auto border-t border-ink-600 bg-ink-900 px-4 pt-4 pb-20 text-ink-100 md:max-h-full md:w-[var(--render-panel-width)] md:border-l md:border-t-0 md:pb-4" aria-label="Инструменты 3D">
    {resize.handle}
    <div role="tablist" aria-label="Режим визуализации" className="mb-4 grid grid-cols-2 gap-1 rounded-lg bg-ink-800 p-1">
      {([['tour','Тур'],['render','Рендер']] as const).map(([value,label])=><button key={value} id={`workflow-tab-${value}`} type="button" role="tab" aria-selected={tab===value} aria-controls={`workflow-${value}`} tabIndex={tab===value?0:-1}
        onClick={()=>setTab(value)} onKeyDown={e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const next=e.key==='Home'?'tour':e.key==='End'?'render':tab==='tour'?'render':'tour';setTab(next);document.getElementById(`workflow-tab-${next}`)?.focus();}}}
        className={`rounded-md px-3 py-2 text-sm font-medium transition-colors duration-200 ${tab===value?'bg-ink-600 text-ink-100 shadow-sm':'text-ink-400 hover:text-ink-100'}`}>{label}</button>)}
    </div>
    <div className="mb-4 grid grid-cols-2 gap-3 text-xs" aria-label="Настройки сцены">
      <label>Сезон<select aria-label="Сезон" value={appearance.season} onChange={e=>onAppearanceChange({...appearance,season:e.target.value as Season})} className="mt-1 block w-full rounded border border-ink-600 bg-ink-800 p-2">{(Object.keys(SEASON_LABELS) as Season[]).map(k=><option key={k} value={k}>{SEASON_LABELS[k][0].toUpperCase()+SEASON_LABELS[k].slice(1)}</option>)}</select></label>
      <label title="Возраст меняет высоту и размер крон только в визуализации">Возраст посадок<select aria-label="Возраст посадок" value={appearance.ageYears??''} onChange={e=>onAppearanceChange({...appearance,ageYears:e.target.value===''?null:Number(e.target.value)})} className="mt-1 block w-full rounded border border-ink-600 bg-ink-800 p-2"><option value="">Зрелые</option><option value="0">При посадке</option><option value="5">5 лет</option><option value="15">15 лет</option><option value="30">30 лет</option></select></label>
    </div>
    <div className="mb-4 text-xs"><label>План<select aria-label="План" disabled={busy||sending||!!loading||!!moving} value={plan?.iterationId||""} onChange={e=>void loadIteration(e.target.value)} className="mt-1 block w-full rounded border border-ink-600 bg-ink-800 p-2">{!plans.length&&<option value="">Сначала создайте план посадок</option>}{plans.map((p,i)=><option value={p.iterationId} key={p.iterationId}>План {i+1} · {p.metrics.treeCount} дер. · {p.metrics.shrubCount} куст.</option>)}</select></label></div>
    <details className="mb-4 rounded border border-ink-600 p-3 text-xs"><summary className="cursor-pointer font-semibold">Геометрия из CAD</summary><label>Высота зданий без данных CAD, м<input aria-label="Высота зданий без данных CAD" type="number" min="2" max="200" value={buildingHeight} onChange={e=>setBuildingHeight(Number(e.target.value))} className="my-2 block w-full rounded border border-ink-600 bg-ink-800 p-2"/></label><label className="my-2 flex items-start gap-2"><input type="checkbox" checked={showFences} onChange={e=>setShowFences(e.target.checked)}/>Моделировать ограждения из CAD (условная высота 1,3 м)</label><Btn disabled={busy||sending||!Number.isFinite(buildingHeight)||buildingHeight<2||buildingHeight>200} onClick={()=>onReconstructionChange({buildingHeightM:buildingHeight,showFences})}>Перестроить 3D-сцену</Btn></details>
    <div id="workflow-render" role="tabpanel" aria-labelledby="workflow-tab-render" hidden={tab!=="render"} className="workflow-panel">
    <fieldset disabled={busy||sending} className="mb-4 grid grid-cols-2 gap-3 text-xs disabled:opacity-60">
      <label className="col-span-2">Пресет<select aria-label="Пресет рендера" value={preset} className="mt-1 block w-full rounded border border-ink-600 bg-ink-800 p-2" onChange={e=>{const v=e.target.value;setPreset(v);if(v==='advertising'){setLighting('sunny');setStrength(.85);}if(v==='natural'){setLighting('daylight');setStrength(.7);}if(v==='evening'){setLighting('evening');setStrength(.8);}}}><option value="advertising">Солнечный · рекламный</option><option value="natural">Естественный · ближе к CAD</option><option value="evening">Вечерний свет</option><option value="custom">Свой вариант</option></select></label>
      <label>Соотношение сторон<select aria-label="Соотношение сторон" value={ratio} onChange={e=>setRatio(e.target.value)} className="mt-1 block w-full rounded border border-ink-600 bg-ink-800 p-2">{['16:9','4:3','1:1','3:4','9:16','viewport'].map(r=><option key={r} value={r}>{r==='viewport'?'Как в редакторе':r}</option>)}</select></label>
      <label>Разрешение<select aria-label="Разрешение рендера" value={resolution} onChange={e=>setResolution(Number(e.target.value))} className="mt-1 block w-full rounded border border-ink-600 bg-ink-800 p-2"><option value={512}>512 px · быстрее</option><option value={768}>768 px · стандарт</option><option value={1024} disabled={!highReady}>1024 px · HD</option><option value={1536} disabled={!highReady}>1536 px · высокое качество</option><option value={2048} disabled={!highReady}>2048 px · 2K</option></select></label>
      <p className="col-span-2 text-2xs text-ink-400">{resolution>=1024?"Высокое качество: ориентировочно 10–20 минут.":"Размер по длинной стороне."}</p>
      <label className="col-span-2">Окружение<select aria-label="Окружение" value={background} onChange={e=>setBackground(e.target.value)} className="mt-1 block w-full rounded border border-ink-600 bg-ink-800 p-2"><option value="source">Геометрия CAD · без добавленного парка</option><option value="park">Зелёное окружение · деревья и газон</option></select></label>
      <label>Освещение<select aria-label="Освещение" value={lighting} onChange={e=>{setLighting(e.target.value);setPreset('custom');}} className="mt-1 block w-full rounded border border-ink-600 bg-ink-800 p-2"><option value="sunny">Солнечное</option><option value="daylight">Дневное</option><option value="evening">Вечернее</option><option value="overcast">Облачное</option></select></label>
      <label>Детализация · {strength.toFixed(2)}<input aria-label="Детализация" type="range" min="0.55" max="0.95" step="0.05" value={strength} onChange={e=>{setStrength(Number(e.target.value));setPreset('custom');}} className="mt-3 w-full accent-emerald-500"/></label>
    </fieldset>
    <div className="flex flex-col items-stretch gap-3 text-xs">

      <label>Ракурс<select aria-label="Ракурс" defaultValue="current" disabled={busy||sending} className="mt-1 block rounded border border-ink-600 bg-ink-800 p-2" onChange={e=>{if(e.target.value!=='current')camera(e.target.value as 'aerial'|'garden');e.currentTarget.value='current';}}><option value="current">Текущая камера</option><option value="aerial">Сверху</option><option value="garden">С уровня человека</option></select></label>
      <Btn onClick={()=>void generate()} disabled={busy||sending||!plan||!!loading||!!moving||!ready||!sceneReady}>{busy?"Генерация…":"Сгенерировать 3D-рендер"}</Btn>
      {moving&&<span className="text-ink-400">Сохраняем перемещение…</span>}
    </div>
    {busy&&<div className="mt-3" aria-live="polite"><div className="mb-1 flex justify-between gap-2 text-xs"><span>{status}</span><span>{progress?.progressPercent||1}% · {minutes(Math.max(0,(deadline-now)/1000))} осталось</span></div><progress aria-label="Прогресс генерации" className="h-2 w-full accent-emerald-500" max="100" value={progress?.progressPercent||1}/></div>}
    {!busy&&status&&<p className="mt-2 text-xs" role="status">{status}</p>}
    {result&&<div className="mt-4 grid gap-4"><div><img src={result.imageUrl} alt="Готовый 3D-рендер выбранного плана" className="max-h-80 w-full rounded bg-ink-800 object-contain"/><div className="mt-1 flex justify-between text-xs text-ink-400"><span>{plans.some(p=>p.iterationId===result.iterationId)?`План ${plans.findIndex(p=>p.iterationId===result.iterationId)+1}`:"Сохранённый план"}</span><a href={result.imageUrl} download className="underline">Скачать{resultSize?` · ${resultSize.width} × ${resultSize.height}`:" рендер"}</a></div></div>
      <div className="flex min-h-40 flex-col"><div className="mb-2 text-sm font-semibold">Улучшить рендер</div><div className="mb-2 max-h-36 space-y-2 overflow-y-auto text-xs" aria-live="polite">{!messages.length&&<p className="text-ink-400">Например: «Сделай свет теплее, а зелень насыщеннее».</p>}{messages.map((m,i)=><p key={i} className={m.role==='user'?"rounded bg-ink-800 p-2":"p-2"}><strong>{m.role==='user'?"Вы":"LLM"}: </strong>{m.content}</p>)}</div>
        <form className="mt-auto flex gap-2" onSubmit={e=>{e.preventDefault();void refine();}}><input aria-label="Пожелание к рендеру" maxLength={1000} value={text} onChange={e=>setText(e.target.value)} disabled={busy||sending||!samePlan} placeholder={samePlan?"Как улучшить результат?":"Сначала создайте рендер выбранного плана"} className="min-w-0 flex-1 rounded border border-ink-600 bg-ink-800 px-3 py-2 text-xs"/><Btn disabled={busy||sending||!samePlan||!text.trim()}>{sending?"LLM…":"Отправить"}</Btn></form>
        {!samePlan&&<p className="mt-1 text-2xs text-ink-400">Выбран другой план — сначала создайте для него рендер.</p>}
      </div></div>}
    </div>
    <div id="workflow-tour" role="tabpanel" aria-labelledby="workflow-tab-tour" hidden={tab!=="tour"} className="workflow-panel">
    <PanoramaPanel active={tab==="tour"} appearance={appearance} projectId={projectId} iterationId={plan?.iterationId} reconstruction={reconstructionSettings} tools={panorama} disabled={!plan||!sceneReady||!!loading||!!moving}/>
    </div>
  </section>;
}
