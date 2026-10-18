import {useState} from "react";

/** Pointer capture keeps resizing stable even when the cursor crosses the canvas. */
export function usePanelResize(key:string, initial:number, edge:"left"|"right") {
  const [width,setWidth]=useState(()=>{try{return Math.max(240,Math.min(560,Number(localStorage.getItem(key))||initial));}catch{return initial;}});
  function update(value:number){const next=Math.round(Math.max(240,Math.min(560,window.innerWidth*.42,value)));setWidth(next);try{localStorage.setItem(key,String(next));}catch{}}
  const handle=<div role="separator" aria-label="Изменить ширину панели" aria-orientation="vertical" aria-valuemin={240} aria-valuemax={560} aria-valuenow={width} tabIndex={0}
    className={`absolute ${edge==='left'?'left-0':'right-0'} top-0 z-40 hidden h-full w-2 cursor-col-resize touch-none hover:bg-emerald-500/40 focus:bg-emerald-500/40 md:block`}
    onDoubleClick={()=>update(initial)} onKeyDown={e=>{if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();update(width+(e.key==='ArrowRight'?1:-1)*(edge==='right'?1:-1)*16);}}}
    onPointerDown={e=>{e.preventDefault();e.currentTarget.setPointerCapture(e.pointerId);e.currentTarget.dataset.start=String(e.clientX);e.currentTarget.dataset.width=String(width);}}
    onPointerMove={e=>{if(e.currentTarget.hasPointerCapture(e.pointerId))update(Number(e.currentTarget.dataset.width)+(e.clientX-Number(e.currentTarget.dataset.start))*(edge==='right'?1:-1));}}
    onPointerUp={e=>{if(e.currentTarget.hasPointerCapture(e.pointerId))e.currentTarget.releasePointerCapture(e.pointerId);}}/>;
  return {width,handle};
}
