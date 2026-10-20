import type { PipelineState } from "./api/types";

/** Russian display names for pipeline states. The codes themselves stay in
 *  English — they are API values and test hooks; only what the user reads is
 *  translated. */
export const PIPELINE_STATE_LABELS: Record<PipelineState, string> = {
  UPLOADED: "ЗАГРУЖЕН",
  CONVERTING: "КОНВЕРТАЦИЯ",
  PARSING: "РАЗБОР",
  CLASSIFYING: "КЛАССИФИКАЦИЯ",
  BUILDING_CONSTRAINTS: "ОГРАНИЧЕНИЯ",
  GENERATING_CANDIDATES: "ГЕНЕРАЦИЯ",
  OPTIMIZING: "ОПТИМИЗАЦИЯ",
  VALIDATING: "ПРОВЕРКА",
  JUDGING: "ОЦЕНКА",
  RERUNNING: "ПОВТОР",
  COMPLETED: "ГОТОВО",
  FAILED: "ОШИБКА",
};

/** Human-readable stage descriptions for progress lines. */
export const PIPELINE_STAGE_TEXT: Partial<Record<PipelineState, string>> = {
  CONVERTING: "Конвертация DWG → DXF",
  PARSING: "Разбор чертежа",
  CLASSIFYING: "Классификация слоёв",
  BUILDING_CONSTRAINTS: "Построение ограничений",
  GENERATING_CANDIDATES: "Генерация кандидатов",
  OPTIMIZING: "Оптимизация",
  VALIDATING: "Проверка по нормам",
  JUDGING: "Оценка качества",
  RERUNNING: "Повторный прогон",
};

/** Judge decision codes → Russian chip text. Optimization meaning only —
 *  never a legal verdict. */
export const JUDGE_DECISION_LABELS: Record<string, string> = {
  ACCEPT: "ПРИНЯТО",
  RERUN: "ПОВТОР",
  MANUAL_REVIEW: "РУЧНАЯ ПРОВЕРКА",
};

/** Who produced the judgment. */
export const JUDGE_SOURCE_LABELS: Record<string, string> = {
  heuristic: "эвристика",
  llm: "LLM",
  mock: "заглушка",
};

export function pipelineStateLabel(s: string | null | undefined): string {
  if (!s) return "";
  return (PIPELINE_STATE_LABELS as Record<string, string>)[s] ?? s;
}

/** Semantic layer / obstacle categories. Values stay as codes in the API and
 *  in <option value>, only the visible text is Russian. */
export const CATEGORY_LABELS: Record<string, string> = {
  SITE_BOUNDARY: "Граница участка",
  BUILDING: "Здание",
  ROAD: "Проезжая часть",
  CURB: "Бортовой камень",
  SIDEWALK: "Тротуар",
  UTILITY: "Сеть (тип не определён)",
  UTILITY_WATER: "Водопровод",
  UTILITY_GAS: "Газопровод",
  UTILITY_ELECTRICITY: "Силовой кабель",
  UTILITY_SEWER: "Канализация",
  UTILITY_HEAT: "Теплосеть",
  UTILITY_DRAINAGE: "Дренаж, водосток",
  UTILITY_COMMUNICATION: "Кабель связи",
  POWER_LINE_OVERHEAD: "Воздушная ЛЭП",
  EXISTING_TREE: "Существующее дерево",
  EXISTING_SHRUB: "Существующий кустарник",
  GREEN_AREA: "Озеленение",
  PARKING: "Парковка",
  ACCESS_ROUTE: "Пожарный проезд",
  FENCE: "Ограждение",
  LIGHTING: "Опора освещения",
  RETAINING_WALL: "Подпорная стенка",
  UNDERGROUND_PARKING: "Подземный паркинг",
  ANNOTATION: "Аннотации",
  UNKNOWN: "Не распознано",
  SPECIES_CROWN: "Крона породы",
  PLANTING_TREE: "Проектное дерево",
  PLANTING_SHRUB: "Проектный кустарник",
};

/** "ROAD,CURB" → "Проезжая часть, Бортовой камень"; unknown codes pass through. */
export function categoryLabel(codes: string | null | undefined): string {
  if (!codes) return "";
  return codes.split(",").map((c) => CATEGORY_LABELS[c.trim()] ?? c.trim()).join(", ");
}

/** Latin genera named by species-specific rules (623-ПП п. 4.2.8). */
export const GENUS_LABELS: Record<string, string> = {
  Tilia: "липа",
  Acer: "клён",
  Syringa: "сирень",
  Lonicera: "жимолость",
  Populus: "тополь",
  Crataegus: "боярышник",
  Cotoneaster: "кизильник",
  Cornus: "дерен",
  Swida: "дерен",
  Larix: "лиственница",
  Betula: "берёза",
};

/** ["Cornus", "Swida", "Larix"] → "дерен, лиственница" (duplicates merged). */
export function genusList(genera: string[]): string {
  return [...new Set(genera.map((g) => GENUS_LABELS[g] ?? g))].join(", ");
}

/** Where a layer's class came from. */
export const CLASS_SOURCE_LABELS: Record<string, string> = {
  rule: "по имени слоя",
  geometry: "по геометрии",
  llm: "локальная LLM",
  user: "задано вручную",
  package: "по файлу пакета",
};

const XREF_KIND_SHORT: Record<string, string> = {
  tp: "топоплан",
  up: "сети",
  pp: "проектные сети",
  kl: "красные линии",
  brd: "рамки съёмки",
};

/** "output[1-18]_3_ДЖКХ-25_03117up$0$Теплосеть" → {source: "сети", layer: "Теплосеть"};
 *  "10000176_Заливки$0$_ГЗН-ГЗН" → {source: "Заливки", …}; plain names → source null. */
export function layerDisplayName(name: string): { source: string | null; layer: string } {
  // our package prefix is everything before the FIRST "$0$"; what follows may
  // carry the designer's own earlier binds ("$0$БР…", "$148$БР…") — drop them
  const clean = (s: string) => s.replace(/^(\$\d+\$)+/, "") || s;
  const i = name.indexOf("$0$");
  if (i <= 0) return { source: null, layer: clean(name) };
  const xref = name.slice(0, i);
  const kind = xref.match(/(tp|up|pp|kl|brd)$/i)?.[1]?.toLowerCase();
  const source = kind ? XREF_KIND_SHORT[kind] : xref.replace(/^[\d_]+/, "") || xref;
  return { source, layer: clean(name.slice(i + 3)) };
}

export const KIND_LABELS: Record<string, string> = {
  TREE: "ДЕРЕВО",
  SHRUB: "КУСТАРНИК",
};

/** Rule source status as stored in the registry. */
export const SOURCE_STATUS_LABELS: Record<string, string> = {
  verified: "подтверждено",
  missing_source_data: "нет источника",
  assumption: "допущение",
  inactive: "не применяется",
  disabled: "выключено",
};

export function sourceStatusLabel(s: string | null | undefined): string {
  if (!s) return "";
  return SOURCE_STATUS_LABELS[s] ?? s;
}

/** Russian plural: plural(5, "замечание", "замечания", "замечаний") → "замечаний". */
export function plural(n: number, one: string, few: string, many: string): string {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}

export const DIMENSION_STATUS_LABELS: Record<string, string> = {
  GOOD: "ХОРОШО",
  IMPROVABLE: "МОЖНО ЛУЧШЕ",
  POOR: "ПЛОХО",
};

export const SEVERITY_LABELS: Record<string, string> = {
  HIGH: "ВЫСОКАЯ",
  MEDIUM: "СРЕДНЯЯ",
  LOW: "НИЗКАЯ",
};

export const DOC_STATUS_LABELS: Record<string, string> = {
  UPLOADED: "ЗАГРУЖЕН",
  PARSED: "РАЗОБРАН",
  EXTRACTED: "ПРАВИЛА ИЗВЛЕЧЕНЫ",
  FAILED: "ОШИБКА",
};

/** Second-pass verifier verdict on an extracted rule. */
export const VERDICT_LABELS: Record<string, string> = {
  SUPPORTED: "ПОДТВЕРЖДЕНО ТЕКСТОМ",
  PARTIALLY_SUPPORTED: "ЧАСТИЧНО",
  UNSUPPORTED: "НЕ ПОДТВЕРЖДЕНО",
  AMBIGUOUS: "НЕОДНОЗНАЧНО",
};

export const CONSTRAINT_TYPE_LABELS: Record<string, string> = {
  MIN_DISTANCE: "мин. расстояние",
  MIN_SPACING: "мин. шаг посадки",
  OTHER: "прочее",
  min_distance: "мин. расстояние",
  min_spacing: "мин. шаг посадки",
  inside: "в границах",
  species_blacklist: "запрещённые породы",
  advisory: "рекомендация",
};
