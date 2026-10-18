"""Local LLM appearance refinement; source plan/camera/guides stay immutable."""
import base64
import json
import uuid
import threading
from typing import Literal
from pydantic import BaseModel
from .llm import LocalLLM
from . import photo_jobs


_history_lock=threading.Lock()


class AppearanceIntent(BaseModel):
    geometryChange:bool
    lighting:Literal['keep','sunny','daylight','evening','overcast']
    palette:Literal['keep','natural','vibrant']
    temperature:Literal['keep','neutral','warm','cool']
    finish:Literal['keep','natural','advertising']
    surfaceStyle:Literal['keep','current','light_stone','warm_stone','dark_stone']
    background:Literal['keep','park','source']


async def interpret(message):
    llm=LocalLLM(timeout=45)
    if not await llm.is_reachable():raise ConnectionError('Локальная LLM недоступна. Попробуйте позже.')
    instructions=("Translate the user's image edit request into JSON controls. For every unmentioned control return keep. "
                  "geometryChange=true ONLY when moving, adding or removing objects is requested. "
                  "Warmer light / теплее means temperature=warm, NOT evening. "
                  "More vivid greens / зелень насыщеннее means palette=vibrant. "
                  "Keep sunshine / сохрани солнечный день means lighting=keep. "
                  "No explanations or nested JSON strings. Return all seven fields.")
    example={'geometryChange':False,'lighting':'keep','palette':'vibrant','temperature':'warm','finish':'keep','surfaceStyle':'keep','background':'keep'}
    return await llm.structured_generate(AppearanceIntent,[{'role':'system','content':instructions},
        {'role':'user','content':'Make the light warmer and the greens more vivid, keep the sunny day.'},
        {'role':'assistant','content':json.dumps(example)}, {'role':'user','content':message}],max_tokens=240)


def apply_intent(settings,intent,message):
    if intent.geometryChange:return {},'Сначала измените состав или расположение объектов на плане, затем создайте новый рендер. Здесь можно изменить свет, цвета и материалы.'
    changes={k:v for k,v in intent.model_dump(exclude={'geometryChange'}).items() if v!='keep' and settings.get(k)!=v}
    # An explicit preservation instruction is a hard user constraint, even if
    # a small local model proposes a contradictory lighting edit.
    lower=message.lower()
    if any(t in lower for t in ['сохрани солнечный','сохранить солнечный','keep the sunny','keep it sunny','keep sunny']):
        changes.pop('lighting',None)
    descriptions={'lighting':{'sunny':'солнечное освещение','daylight':'дневной свет','evening':'вечерний свет','overcast':'мягкий пасмурный свет'},
        'palette':{'natural':'естественные цвета','vibrant':'более насыщенную зелень'},
        'temperature':{'warm':'более тёплый тон','cool':'более прохладный тон','neutral':'нейтральный тон'},
        'finish':{'natural':'естественную подачу','advertising':'рекламную подачу'},
        'surfaceStyle':{'current':'исходные материалы','light_stone':'светлый камень на существующих покрытиях','warm_stone':'тёплый камень на существующих покрытиях','dark_stone':'тёмный камень на существующих покрытиях'},
        'background':{'park':'зелёное окружение за участком','source':'фон исходной сцены'}}
    reply=('Применю '+', '.join(descriptions[k][v] for k,v in changes.items())+'. План и камера сохраняются.') if changes else 'Уточните, что изменить в освещении, цветах или материалах.'
    return changes,reply


def history(pid,jid):
    folder=photo_jobs.root(pid,jid)
    if not (folder/'settings.json').is_file():raise FileNotFoundError()
    path=folder/'chat.json'
    if path.is_file():return json.loads(path.read_text())
    return json.loads((folder/'settings.json').read_text()).get('conversation',[])


async def refine(pid,jid,message):
    if photo_jobs.status(pid,jid)['state']!='COMPLETED':raise ValueError('Дождитесь готового рендера.')
    folder=photo_jobs.root(pid,jid)
    settings=json.loads((folder/'settings.json').read_text())
    intent=await interpret(message)
    changes,reply=apply_intent(settings,intent,message)
    with _history_lock:
        conversation=[*history(pid,jid),{'role':'user','content':message},{'role':'assistant','content':reply}][-20:]
        temporary=folder/f'.chat-{uuid.uuid4().hex}.tmp'
        temporary.write_text(json.dumps(conversation,ensure_ascii=False));temporary.replace(folder/'chat.json')
    if not changes:return {'reply':reply,'conversation':conversation,'job':None}
    settings.update(changes)
    settings.update(parentJobId=jid,conversation=conversation)
    for field,filename in [('image','source.png'),('depth','depth.png'),('semantic','semantic.png')]:
        settings[field]='data:image/png;base64,'+base64.b64encode((folder/filename).read_bytes()).decode()
    child=photo_jobs.submit(pid,settings)
    return {'reply':reply,'conversation':conversation,'job':photo_jobs.urls(pid,child)}
