"""Layer/entity semantic classification.

Pipeline: deterministic keyword rules → geometry heuristics → (optional) local
LLM for still-unknown layers. Confidence is always preserved and uncertainty is
never hidden — layers below the review threshold get requiresReview=True and
can be overridden from the UI (source="user").
"""
from __future__ import annotations

import re
from ..models.ir import CadDocument, LayerClassification

REVIEW_THRESHOLD = 0.65

# (regex, category, confidence). Order matters — first match wins per group;
# groups are evaluated top to bottom and the best-confidence match is kept.
_KEYWORD_RULES: list[tuple[str, str, float]] = [
    # --- disambiguation first (more specific beats generic 'границ') ---
    (r"границ[а-я]*\s*(растительн|леса|газон)", "GREEN_AREA", 0.92),
    (r"границ[а-я]*\s*улиц", "ROAD", 0.91),
    (r"номер\s*(дома|строен|здан)|номер\b", "ANNOTATION", 0.86),
    # "Кабель связи" is a comms cable, but bare 'кабел' (electricity, 0.86)
    # outscores bare 'связ' (0.84), so name the combination explicitly.
    (r"кабел[а-я]*\s*(связи|телефон|телеграф)|волоконно[\s-]*оптич", "UTILITY_COMMUNICATION", 0.88),
    # --- site boundary ---
    # \bотвод, not bare 'отвод': without the word boundary it swallows
    # "Водоотводные лотки" (a drain) and files it as a site boundary.
    (r"границ|\bотвод|красн[аы]|благоустройств[а-я]*\s*границ|гпзу|land\s*boundary|site.?bound", "SITE_BOUNDARY", 0.9),
    # --- buildings ---
    (r"здан|строен|сооруж|корпус|дом[а-я]*\b|фундамент|отмостк|крыльц|build|wall|house", "BUILDING", 0.85),
    (r"кровл|контур\s*здан", "BUILDING", 0.8),
    # --- roads / transport ---
    (r"проезж|дорог|проезд|асфальт|а\/б|улиц|магистрал|road|driveway|carriage", "ROAD", 0.85),
    (r"борт|бордюр|curb|бр\s?100", "CURB", 0.85),
    # granite curb grades ГП1 (300x150), ГП 2 (450x180)… — anchored, so the
    # "ДВ_ГП_П_…" (design general plan) layer prefix never matches
    (r"^гп\s*\d", "CURB", 0.85),
    (r"тротуар|пешеход|дорожк|плитк|мощени|sidewalk|footpath|path", "SIDEWALK", 0.85),
    (r"парковк|стоянк|машином|parking", "PARKING", 0.85),
    (r"пожарн[а-я]*\s*(проезд|подъезд)|подъезд", "ACCESS_ROUTE", 0.8),
    # --- utilities (specific first) ---
    (r"водопров|водоснабж|\bв1\b|\bхвс\b|\bгвс\b|water", "UTILITY_WATER", 0.86),
    (r"газопров|газоснабж|\bгаз\b|gas", "UTILITY_GAS", 0.86),
    # ливневая (дождевая) канализация К2 = канализация по семантике СП 42
    # табл. 9.1 (1.5 м), НЕ дренаж (2.0 м) — держим её в UTILITY_SEWER.
    # 'водосток' — так Мосгеотрест подписывает ту же ливнёвку; это самый
    # крупный слой в выгрузке ИРД «up» (существующие подземные сети).
    (r"канализ|\bк1\b|\bк2\b|фекальн|бытов[а-я]*\s*сток|ливн|дожд|водосток|sewer|storm|хозбыт", "UTILITY_SEWER", 0.86),
    (r"дренаж|водоотвод|drain", "UTILITY_DRAINAGE", 0.84),
    (r"теплосет|теплотрасс|тепло|\bтс\b|heat", "UTILITY_HEAT", 0.84),
    (r"кабел|\bкл\b|кл[\s\-_]?0[.,]4|кл[\s\-_]?10|электро|эл\.?снабж|силов|лэп|power|electric|0[.,]4\s*кв|10\s*кв", "UTILITY_ELECTRICITY", 0.86),
    (r"связ|телефон|телеграф|тлф|слаботоч|волокон|вол[сc]|telecom|comm", "UTILITY_COMMUNICATION", 0.84),
    (r"сет[ии]|коммуникац|инжен|колодц|люк[и]?\b|коллектор|трубопровод|utility", "UTILITY", 0.55),
    # --- vegetation ---
    (r"дерев|деревья|сущ[а-я.]*\s*(зелен|насажд)|tree", "EXISTING_TREE", 0.85),
    (r"кустарн|куст|shrub|bush", "EXISTING_SHRUB", 0.85),
    (r"газон|озелен|зелен|насажд|цветник|lawn|green|vegetation|planting", "GREEN_AREA", 0.75),
    # --- misc infrastructure ---
    (r"огражден|оград|забор|fence", "FENCE", 0.85),
    (r"освещ|фонар|светильн|опор[аы]\s*осв|light", "LIGHTING", 0.8),
    (r"светофор|столб", "LIGHTING", 0.7),
    # --- Mosgeotrest topographic plan (ИРД "tp") vocabulary ---------------
    # structures → the "наружная стена здания и сооружения" setback applies
    (r"павильон|навес|вышк[аи]|вентилятор|фонтан|памятник", "BUILDING", 0.75),
    # "Опоры контакной сети" (sic in the source): tram / trolleybus poles —
    # the "мачта и опора … трамвая" row of СП 42 табл. 9.1
    (r"опор[аы]\s*конта[кт]*н", "LIGHTING", 0.8),
    (r"колодц|\bлюк", "UTILITY", 0.7),
    (r"выгребн", "UTILITY_SEWER", 0.7),
    (r"парапет", "FENCE", 0.7),
    (r"лестниц", "SIDEWALK", 0.7),
    (r"\bмост", "ROAD", 0.7),
    # relief, survey marks, signs and small architectural forms: drawn
    # objects with no normative planting setback
    (r"откос|внутренн[а-я]*\s*заполнен|топографическ|геодезическ|берегов"
     r"|громоотвод|указател|полусфер|пояснительн", "ANNOTATION", 0.75),
    (r"маф\b|маф_|малы[ех]\s*архитектурн", "ANNOTATION", 0.7),
    # --- annotation / drafting ---
    (r"размер|текст|надпис|выноск|обознач|услов|экспл[иу]кац|штамп|рамк|таблиц|легенд|примечан"
     r"|горизонтал|отметк"
     r"|оси\b|осей|координат|сетк[аи]|defpoints|dim|text|annot|label|title|frame|axis|grid", "ANNOTATION", 0.8),
]

_COMPILED = [(re.compile(rx, re.IGNORECASE), cat, conf) for rx, cat, conf in _KEYWORD_RULES]

# Block-name hints for INSERT-heavy layers (tree symbols etc.)
_BLOCK_RULES: list[tuple[str, str, float]] = [
    (r"дерев|tree|листв|хвойн", "EXISTING_TREE", 0.8),
    (r"куст|shrub", "EXISTING_SHRUB", 0.8),
    (r"свет|фонар|light|lamp", "LIGHTING", 0.7),
]
_BLOCK_COMPILED = [(re.compile(rx, re.IGNORECASE), cat, conf) for rx, cat, conf in _BLOCK_RULES]


# AutoCAD BIND names xref layers "<xref>$0$<layer>" (also "$130$<layer>");
# the meaning is in the last part.
_BIND_PREFIX = re.compile(r"^.*\$\d+\$")

# Moscow general-plan cover fills ("заливки"): "_<было>-<стало>", e.g.
# "_АБ ТР-ГЗН" = asphalt sidewalk becomes lawn. The part after the dash is
# what the project builds: ГЗН газон, ПЧ проезжая часть, ТР тротуар,
# ПЛ плитка, Щ щебень (hard surface).
_COVER_RX = re.compile(r"^_\s*([А-ЯЁA-Z ]+?)\s*-\s*([А-ЯЁA-Z ]+?)\s*(\(.*\))?\s*$")


def _cover_transition(name: str) -> LayerClassification | None:
    m = _COVER_RX.match(name)
    if not m:
        return None
    new = m.group(2).upper().split()
    if "ГЗН" in new:
        cat = "GREEN_AREA"
    elif "ПЧ" in new:
        cat = "ROAD"
    elif "ТР" in new or "ПЛ" in new or new == ["Щ"]:
        cat = "SIDEWALK"
    else:
        return None
    return LayerClassification(category=cat, confidence=0.9, source="rule",
                               requiresReview=False, matchedKeyword=f"cover:{m.group(2).strip()}")


def classify_layer_name(name: str) -> LayerClassification:
    name = _BIND_PREFIX.sub("", name)
    cover = _cover_transition(name)
    if cover is not None:
        return cover
    best: tuple[str, float, str] | None = None
    for rx, cat, conf in _COMPILED:
        m = rx.search(name)
        if m and (best is None or conf > best[1]):
            best = (cat, conf, m.group(0))
    if best:
        cat, conf, kw = best
        return LayerClassification(category=cat, confidence=conf, source="rule",
                                   requiresReview=conf < REVIEW_THRESHOLD, matchedKeyword=kw)
    return LayerClassification(category="UNKNOWN", confidence=0.3, source="rule", requiresReview=True)


def _geometry_hint(doc: CadDocument, layer: str, ents: list | None = None) -> LayerClassification | None:
    """Heuristics for layers keyword rules could not resolve."""
    if ents is None:
        ents = [e for e in doc.entities if e.layer == layer]
    if not ents:
        return None
    texts = sum(1 for e in ents if e.kind == "text")
    if texts / len(ents) > 0.7:
        return LayerClassification(category="ANNOTATION", confidence=0.6, source="geometry", requiresReview=True)
    # many small circles / block inserts → likely tree symbols
    small_round = 0
    for e in ents:
        r = e.geometry.get("radius")
        if e.type == "INSERT" or (r is not None and 0.2 <= r <= 8.0):
            small_round += 1
    if small_round >= 5 and small_round / len(ents) > 0.6:
        return LayerClassification(category="EXISTING_TREE", confidence=0.5, source="geometry", requiresReview=True)
    # block-name hints
    for e in ents[:50]:
        if e.blockName:
            for rx, cat, conf in _BLOCK_COMPILED:
                if rx.search(e.blockName):
                    return LayerClassification(category=cat, confidence=conf, source="geometry",
                                               requiresReview=conf < REVIEW_THRESHOLD,
                                               matchedKeyword=f"block:{e.blockName}")
    return None


def classify_document(doc: CadDocument) -> None:
    """Mutates doc.layers in place with rule + geometry classification."""
    by_layer: dict[str, list] = {}
    for e in doc.entities:  # one pass — merged packages carry 300k+ entities
        by_layer.setdefault(e.layer, []).append(e)
    for li in doc.layers:
        cls = classify_layer_name(li.name)
        if cls.category == "UNKNOWN":
            hint = _geometry_hint(doc, li.name, by_layer.get(li.name, []))
            if hint:
                cls = hint
        li.classification = cls


async def refine_with_llm(doc: CadDocument, llm) -> list[str]:
    """Optionally ask the local LLM to classify still-uncertain layers.

    Sends ONLY compact layer summaries (name, entity type histogram, sample
    texts) — never raw CAD data. Returns list of layer names updated.
    """
    from pydantic import BaseModel

    class LayerGuess(BaseModel):
        layer: str
        category: str
        confidence: float

    class LayerGuesses(BaseModel):
        guesses: list[LayerGuess]

    pending = [li for li in doc.layers
               if li.classification.requiresReview and li.classification.confidence < 0.6]
    if not pending or llm is None or not await llm.is_reachable():
        return []

    from ..models.ir import CATEGORIES
    summaries = []
    for li in pending[:20]:
        ents = [e for e in doc.entities if e.layer == li.name]
        types: dict[str, int] = {}
        for e in ents:
            types[e.type] = types.get(e.type, 0) + 1
        texts = [e.geometry.get("text", "") for e in ents if e.kind == "text"][:5]
        summaries.append({"layer": li.name, "entityTypes": types, "sampleTexts": texts})

    try:
        result = await llm.structured_generate(
            LayerGuesses,
            [{"role": "system",
              "content": "You classify CAD layer names from Russian site plans (стройгенплан, "
                         "благоустройство). Allowed categories: " + ", ".join(CATEGORIES) +
                         ". Respond with JSON only."},
             {"role": "user",
              "content": "Classify each layer:\n" + repr(summaries)}],
        )
    except Exception:
        return []

    updated = []
    lm = doc.layer_map()
    for g in result.guesses:
        li = lm.get(g.layer)
        if li and g.category in CATEGORIES and li.classification.source != "user":
            conf = max(0.0, min(0.95, float(g.confidence)))
            if conf > li.classification.confidence:
                li.classification = LayerClassification(
                    category=g.category, confidence=conf, source="llm",
                    requiresReview=conf < REVIEW_THRESHOLD)
                updated.append(li.name)
    return updated
