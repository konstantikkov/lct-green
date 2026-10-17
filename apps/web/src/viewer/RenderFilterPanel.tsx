import {useState} from 'react';
import {DEFAULT_FILTERS,FILTER_PRESETS,type RenderFilters} from './renderFilters';
export function RenderFilterPanel({value,onChange}:{value:RenderFilters;onChange:(value:RenderFilters)=>void}) {
  const [open,setOpen]=useState(false);
  const controls:{key:keyof RenderFilters;label:string;min:number;max:number;step:number}[]=[
    {key:'exposure',label:'Экспозиция',min:-1,max:1,step:.05},
    {key:'contrast',label:'Контраст',min:.8,max:1.4,step:.02},
    {key:'saturation',label:'Насыщенность',min:0,max:1.5,step:.05},
    {key:'warmth',label:'Теплота',min:-.3,max:.3,step:.02},
    {key:'vignette',label:'Виньетка',min:0,max:.6,step:.02},
    {key:'clarity',label:'Чёткость',min:0,max:.6,step:.02},
  ];
  return <div className="absolute right-2 top-36 z-20 md:top-20">
    <button className="rounded border border-ink-600 bg-ink-900 px-3 py-1.5 text-xs text-ink-100 shadow" onClick={()=>setOpen(!open)} aria-expanded={open}>Фильтры</button>
    {open&&<div className="absolute right-0 top-full mt-1 max-h-[60vh] w-72 max-w-[calc(100vw-2rem)] overflow-y-auto rounded border border-ink-600 bg-ink-900 p-3 text-xs text-ink-100 shadow-xl" role="group" aria-label="Фильтры рендера">
      <div className="mb-3 flex justify-between"><strong>Фильтры рендера</strong><button aria-label="Закрыть фильтры" onClick={()=>setOpen(false)}>✕</button></div>
      <div className="mb-3 flex flex-wrap gap-1">{Object.entries(FILTER_PRESETS).map(([label,preset])=><button key={label} className="rounded border border-ink-600 px-2 py-1 hover:bg-ink-800" onClick={()=>onChange({...preset})}>{label}</button>)}</div>
      {controls.map(c=><label key={c.key} className="mb-2 block"><span className="flex justify-between"><span>{c.label}</span><span>{value[c.key].toFixed(2)}</span></span><input aria-label={c.label} className="w-full" type="range" min={c.min} max={c.max} step={c.step} value={value[c.key]} onChange={e=>onChange({...value,[c.key]:Number(e.target.value)})}/></label>)}
      <button className="mt-2 rounded border border-ink-600 px-3 py-1" onClick={()=>onChange({...DEFAULT_FILTERS})}>Сбросить</button>
      <p className="mt-2 text-ink-400">Сохраняются в «Снимок». Цвета исходного CAD и DXF не меняются.</p>
    </div>}
  </div>;
}
