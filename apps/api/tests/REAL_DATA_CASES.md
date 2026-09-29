# Регрессионные случаи с пилотных данных

Каждая ошибка, найденная на реальных данных набора «Пилотный проект 20 улиц», воспроизводится маленьким синтетическим чертежом в тестах. Такой тест проходит за доли секунды и не даёт ошибке вернуться. Новую ошибку с реальных данных фиксируйте так же: сначала мини-чертёж, который её воспроизводит, потом исправление.

## Пакеты с внешними ссылками

| Объект | Что было | Причина | Тест |
|---|---|---|---|
| Олимпийская деревня | Коммуникации не доходили до плана | Топоплан и сети подключены к генплану внешними ссылками | `test_package.py::test_sources_resolved_and_classified_by_kind`, `test_layers_renamed_like_autocad_bind` |
| Олимпийская деревня | Граница участка не находилась | Граница работ нарисована двумя незамкнутыми половинами | `test_package.py::test_site_is_the_stitched_work_boundary_and_domain_the_lawns` |
| Харьковский проезд | Нет файла границ работ | Граница — слой внутри основного чертежа | `test_package.py::test_work_boundary_as_a_layer_of_the_base_drawing` |
| Макеева | Сети не подключались | Ссылки второго уровня: генплан → `xref_ИТП` → `tp_`/`up_` | `test_package.py::test_nested_xrefs_are_merged` |
| Макеева | ИРД не распознавались | Суффикс в начале имени: `tp_Макеева…` | `test_package.py::test_source_kinds_other_pilot_namings` |
| Макеева | Слой «_за границами работ» принимался за границу | Ключевое слово «границ» без учёта «за» | `test_package.py::test_source_kinds_other_pilot_namings`, словарь в `test_unit.py` |
| 3-я Парковая | Топоплан не подключался | ИРД лежит рядом, но не подключён к генплану | `test_package.py::test_unattached_ird_drawing_is_merged` |
| Багрицкого | Основной чертёж выбирался неверно | Несколько отчётов eTransmit | `test_package.py::test_base_from_the_richest_report_next_to_its_drawing` |
| Харьковский проезд | Выгрузка «разъезжалась» на 5 км | В генплане копия контура границы работ в стороне от плана | `test_package.py::test_empty_copy_of_the_work_boundary_is_not_a_site_part` |
| Харьковский проезд | Расчёт падал на `exterior` | Участок из нескольких частей (MultiPolygon) | `test_package.py::test_distance_to_a_multipart_site_boundary` |

## Конвертация DWG и чтение DXF

| Объект | Что было | Причина | Тест |
|---|---|---|---|
| Харьковский проезд | Топоплан и план освещения «объединены», но пусты | LibreDWG теряет пространство модели | `test_package.py::test_model_space_lost_by_the_converter_is_detected` |
| Харьковский проезд | План освещения не читался | Лист со ссылкой на несуществующую запись блока | `test_package.py::test_layout_without_block_record_is_dropped` |
| Несколько объектов | ezdxf отказывался читать вывод LibreDWG | Безымянные записи таблиц, неверный тип колонок MTEXT | `test_package.py::test_unreadable_libredwg_output_is_repaired` |
| 3-я Парковая | Ошибка 500 при сохранении | Байты cp1251 внутри UTF-8 DXF | `test_package.py::test_undecodable_cad_text_is_cleaned` |
| Любой | Файл не читался ODA | Нужен запасной конвертер | `test_package.py::test_oda_failure_falls_back_to_libredwg` |

## Геометрия и нормы

| Объект | Что было | Причина | Тест |
|---|---|---|---|
| Олимпийская деревня | Газоны пропадали из области посадки | Замкнутые кольца бортового камня считались залитыми площадями | `test_lawn.py::test_closed_curb_ring_is_a_line_not_an_area` |
| Олимпийская деревня | Газонные островки внутри асфальта считались асфальтом | У заливок терялись внутренние контуры | `test_lawn.py::test_hatch_island_is_a_hole` |
| Макеева, Олимпийская, Багрицкого | Охранная зона ЛЭП бусами вокруг опор | В топоплане есть знаки у опор, а провода не нарисованы | `test_power_lines.py::test_support_symbols_are_joined_into_the_wire` |
| Олимпийская деревня | ЛЭП считалась подземным кабелем | Общее ключевое слово с кабелем | `test_power_lines.py::test_overhead_line_is_not_a_cable` |
| Олимпийская деревня | Куст «нарушал» правило для деревьев | Зоны правил для деревьев применялись к кустам | `test_rejections.py::test_shrub_is_not_blamed_on_a_tree_rule` |

## Выгрузка для AutoCAD

| Объект | Что было | Причина | Тест |
|---|---|---|---|
| Олимпийская деревня | AutoCAD Web: «испорченный DXF» | Висящие ссылки на объекты исходных файлов | `test_package.py::test_merged_package_is_autocad_safe` |
| Олимпийская деревня | То же | Сложные типы линий ссылались на чужие стили | `test_package.py::test_complex_linetype_every_shape_element_repointed` |
| Харьковский проезд | Вставки несуществующих блоков | LibreDWG не сконвертировал блок стрелки размера | `test_package.py::test_export_drops_inserts_of_missing_blocks` |

## Проверки сквозного прогона

Автоматические проверки результата (`scripts/invariants.py`, `scripts/check_dxf_output.py`) сами покрыты тестами `test_invariants.py`, кэш загрузки — `test_cache.py`. Сквозной прогон пилотных объектов (`scripts/e2e_run.py`) сверяет результаты с эталонными метриками в `tests/golden/e2e/`.
