#!/usr/bin/env python3
"""
Рекурсивная конвертация дерева DWG -> DXF с сохранением структуры папок.

Ищет все *.dwg в указанной папке и её подпапках, конвертирует каждый файл
в несколько версий DXF и раскладывает результат в параллельное дерево папок.
Папки, в поддереве которых нет ни одного DWG, в выходном дереве не создаются.

DWG — закрытый бинарный формат, поэтому нужен внешний движок конвертации:
  * ODA File Converter (https://www.opendesign.com/guestfiles/oda_file_converter)
  * LibreDWG (утилита dwg2dxf)

Скрипт сам находит доступный движок. Внешних Python-зависимостей нет.
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Версии DXF
# ---------------------------------------------------------------------------

# Порядок — от новых к старым.
ODA_VERSIONS = [
    "ACAD2018",
    "ACAD2013",
    "ACAD2010",
    "ACAD2007",
    "ACAD2004",
    "ACAD2000",
    "ACAD14",
    "ACAD13",
    "ACAD12",
]

DEFAULT_VERSIONS = ODA_VERSIONS[:3]  # три самые свежие

# dwg2dxf принимает версии в своей нотации.
LIBREDWG_VERSION = {
    "ACAD2018": "r2018",
    "ACAD2013": "r2013",
    "ACAD2010": "r2010",
    "ACAD2007": "r2007",
    "ACAD2004": "r2004",
    "ACAD2000": "r2000",
    "ACAD14": "r14",
    "ACAD12": "r12",
}


# ---------------------------------------------------------------------------
# Вспомогательное
# ---------------------------------------------------------------------------

_print_lock = threading.Lock()


def log(message: str) -> None:
    with _print_lock:
        print(message, flush=True)


def is_within(path: Path, parent: Path) -> bool:
    """True, если path лежит внутри parent (или совпадает с ним)."""
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def find_case_insensitive(directory: Path, filename: str) -> Path | None:
    """Найти файл в каталоге без учёта регистра имени."""
    exact = directory / filename
    if exact.exists():
        return exact
    wanted = filename.lower()
    try:
        for entry in directory.iterdir():
            if entry.is_file() and entry.name.lower() == wanted:
                return entry
    except OSError:
        pass
    return None


def prune_empty_dirs(root: Path) -> None:
    """Снести пустые каталоги в выходном дереве (страховка после ошибок)."""
    if not root.is_dir():
        return
    for dirpath, _dirnames, _filenames in os.walk(root, topdown=False):
        directory = Path(dirpath)
        try:
            if not any(directory.iterdir()):
                directory.rmdir()
        except OSError:
            pass


def move_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    shutil.move(str(src), str(dst))


# ---------------------------------------------------------------------------
# Движки конвертации
# ---------------------------------------------------------------------------


class ConversionError(Exception):
    pass


class Engine:
    """Базовый интерфейс движка."""

    name = "engine"

    def convert_group(
        self,
        src_dir: Path,
        jobs: dict[str, Path],
        version: str,
        timeout: float,
    ) -> dict[str, str | None]:
        """Сконвертировать файлы одной папки.

        jobs: {имя_файла.dwg: абсолютный путь к целевому .dxf}
        Возвращает {имя_файла.dwg: None при успехе либо текст ошибки}.
        """
        raise NotImplementedError


class OdaEngine(Engine):
    """ODA File Converter — пакетный, работает по папкам целиком.

    Командная строка:
        ODAFileConverter <вход> <выход> <версия> <тип> <рекурсия> <аудит> [фильтр]
    Код возврата ненадёжен, поэтому успех проверяется по наличию файлов.
    """

    name = "ODA File Converter"

    def __init__(self, exe: Path, audit: bool = False) -> None:
        self.exe = exe
        self.audit = audit

    def convert_group(self, src_dir, jobs, version, timeout):
        tmp_out = Path(tempfile.mkdtemp(prefix="dwg2dxf_"))
        try:
            cmd = [
                str(self.exe),
                str(src_dir),
                str(tmp_out),
                version,
                "DXF",
                "0",  # без рекурсии: дерево обходим сами
                "1" if self.audit else "0",
                "*.DWG",
            ]
            env = os.environ.copy()
            if sys.platform != "win32":
                # ODA File Converter — Qt-приложение, на headless-машине
                # без этого не стартует.
                env.setdefault("QT_QPA_PLATFORM", "offscreen")

            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=timeout,
                    env=env,
                )
                tail = (proc.stderr or proc.stdout or "").strip().splitlines()
                hint = tail[-1] if tail else f"код возврата {proc.returncode}"
            except subprocess.TimeoutExpired:
                hint = f"таймаут {timeout:.0f} с"
            except OSError as exc:
                hint = f"не удалось запустить конвертер: {exc}"

            results: dict[str, str | None] = {}
            for dwg_name, target in jobs.items():
                produced = find_case_insensitive(
                    tmp_out, Path(dwg_name).stem + ".dxf"
                )
                if produced is None:
                    results[dwg_name] = f"ODA не создал DXF ({hint})"
                    continue
                try:
                    move_file(produced, target)
                except OSError as exc:
                    results[dwg_name] = f"не удалось записать результат: {exc}"
                else:
                    results[dwg_name] = None
            return results
        finally:
            shutil.rmtree(tmp_out, ignore_errors=True)


class LibreDwgEngine(Engine):
    """LibreDWG dwg2dxf — работает по одному файлу."""

    name = "LibreDWG (dwg2dxf)"

    def __init__(self, exe: Path) -> None:
        self.exe = exe

    def convert_group(self, src_dir, jobs, version, timeout):
        as_version = LIBREDWG_VERSION.get(version)
        results: dict[str, str | None] = {}
        # Пишем во временный каталог и переносим только успешный результат,
        # чтобы при ошибке в выходном дереве не оставалось пустых папок.
        tmp_dir = Path(tempfile.mkdtemp(prefix="dwg2dxf_"))
        try:
            for dwg_name, target in jobs.items():
                tmp_out = tmp_dir / (Path(dwg_name).stem + ".dxf")
                cmd = [str(self.exe)]
                if as_version:
                    cmd += ["--as", as_version]
                cmd += ["-o", str(tmp_out), str(src_dir / dwg_name)]

                try:
                    proc = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        errors="replace",
                        timeout=timeout,
                    )
                except subprocess.TimeoutExpired:
                    results[dwg_name] = f"таймаут {timeout:.0f} с"
                    continue
                except OSError as exc:
                    results[dwg_name] = f"не удалось запустить конвертер: {exc}"
                    continue

                if tmp_out.exists() and tmp_out.stat().st_size > 0:
                    try:
                        move_file(tmp_out, target)
                    except OSError as exc:
                        results[dwg_name] = f"не удалось записать результат: {exc}"
                    else:
                        results[dwg_name] = None
                else:
                    tail = (proc.stderr or proc.stdout or "").strip().splitlines()
                    hint = tail[-1] if tail else f"код возврата {proc.returncode}"
                    tmp_out.unlink(missing_ok=True)
                    results[dwg_name] = f"dwg2dxf не создал DXF ({hint})"
            return results
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Поиск движка
# ---------------------------------------------------------------------------


def find_oda_converter(explicit: str | None = None) -> Path | None:
    if explicit:
        path = Path(explicit)
        return path if path.is_file() else None

    env_path = os.environ.get("ODA_FILE_CONVERTER")
    if env_path and Path(env_path).is_file():
        return Path(env_path)

    exe_name = "ODAFileConverter.exe" if sys.platform == "win32" else "ODAFileConverter"
    on_path = shutil.which(exe_name) or shutil.which("ODAFileConverter")
    if on_path:
        return Path(on_path)

    roots = [
        Path(r"C:\Program Files\ODA"),
        Path(r"C:\Program Files (x86)\ODA"),
        Path("/usr/bin"),
        Path("/usr/local/bin"),
        Path("/opt/ODA"),
        Path("/Applications"),
    ]
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        found.extend(root.glob(f"*/{exe_name}"))
        found.extend(root.glob(f"*/*/{exe_name}"))
        candidate = root / exe_name
        if candidate.is_file():
            found.append(candidate)
    if not found:
        return None
    # Свежая версия обычно идёт последней по алфавиту имени каталога.
    return sorted(found, key=lambda p: str(p).lower())[-1]


def find_libredwg() -> Path | None:
    on_path = shutil.which("dwg2dxf")
    return Path(on_path) if on_path else None


def build_engine(args) -> Engine:
    wanted = args.engine
    oda = find_oda_converter(args.oda_path) if wanted in ("auto", "oda") else None
    libre = find_libredwg() if wanted in ("auto", "libredwg") else None

    if wanted == "oda":
        if oda is None:
            raise SystemExit(
                "ODA File Converter не найден. Укажите путь через --oda-path "
                "или переменную окружения ODA_FILE_CONVERTER."
            )
        return OdaEngine(oda, audit=args.audit)

    if wanted == "libredwg":
        if libre is None:
            raise SystemExit("dwg2dxf (LibreDWG) не найден в PATH.")
        return LibreDwgEngine(libre)

    if oda is not None:
        return OdaEngine(oda, audit=args.audit)
    if libre is not None:
        return LibreDwgEngine(libre)

    raise SystemExit(
        "Не найден ни один движок конвертации DWG.\n"
        "Установите один из вариантов:\n"
        "  1) ODA File Converter (рекомендуется) — бесплатно, все версии DWG:\n"
        "     https://www.opendesign.com/guestfiles/oda_file_converter\n"
        "     После установки путь определится автоматически, либо задайте\n"
        "     --oda-path \"C:\\Program Files\\ODA\\<версия>\\ODAFileConverter.exe\"\n"
        "  2) LibreDWG — утилита dwg2dxf должна быть доступна в PATH."
    )


# ---------------------------------------------------------------------------
# Обход дерева и планирование работы
# ---------------------------------------------------------------------------


@dataclass
class Task:
    src_dir: Path
    version: str
    jobs: dict[str, Path] = field(default_factory=dict)


def target_path(
    out_root: Path, rel_dir: Path, stem: str, version: str, layout: str
) -> Path:
    if layout == "flat":
        return out_root / rel_dir / f"{stem}.{version}.dxf"
    return out_root / version / rel_dir / f"{stem}.dxf"


def collect_dwg_dirs(root: Path, out_root: Path) -> list[tuple[Path, list[str]]]:
    """Вернуть [(папка, [имена dwg])] — только папки, где DWG есть напрямую."""
    groups: list[tuple[Path, list[str]]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        # Не заходим в выходное дерево, если оно оказалось внутри входного.
        dirnames[:] = sorted(
            d for d in dirnames if not is_within(current / d, out_root)
        )
        names = sorted(
            f
            for f in filenames
            if f.lower().endswith(".dwg") and not f.startswith(("~$", "."))
        )
        if names:
            groups.append((current, names))
    return groups


def plan_tasks(
    root: Path,
    out_root: Path,
    versions: list[str],
    layout: str,
    skip_existing: bool,
) -> tuple[list[Task], int]:
    groups = collect_dwg_dirs(root, out_root)
    tasks: list[Task] = []
    skipped = 0
    for src_dir, names in groups:
        rel_dir = src_dir.relative_to(root)
        for version in versions:
            jobs: dict[str, Path] = {}
            for name in names:
                target = target_path(
                    out_root, rel_dir, Path(name).stem, version, layout
                )
                if skip_existing and target.exists() and target.stat().st_size > 0:
                    skipped += 1
                    continue
                jobs[name] = target
            if jobs:
                tasks.append(Task(src_dir=src_dir, version=version, jobs=jobs))
    return tasks, skipped


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="dwg2dxf_tree",
        description=(
            "Рекурсивно конвертирует DWG в DXF, повторяя структуру папок "
            "в отдельном выходном дереве."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Примеры:\n"
            "  python dwg2dxf_tree.py C:\\proj\\drawings\n"
            "  python dwg2dxf_tree.py C:\\proj\\drawings -o D:\\out --versions ACAD2018\n"
            "  python dwg2dxf_tree.py C:\\proj\\drawings --dry-run\n"
        ),
    )
    parser.add_argument("input", type=Path, help="папка с DWG (обходится рекурсивно)")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="выходная папка (по умолчанию — рядом со входной, с суффиксом _dxf)",
    )
    parser.add_argument(
        "--versions",
        nargs="+",
        default=DEFAULT_VERSIONS,
        metavar="VER",
        help=(
            "версии DXF на выходе; по умолчанию три самые свежие: "
            + " ".join(DEFAULT_VERSIONS)
            + ". Доступно: "
            + ", ".join(ODA_VERSIONS)
        ),
    )
    parser.add_argument(
        "--layout",
        choices=("version-dirs", "flat"),
        default="version-dirs",
        help=(
            "version-dirs (по умолчанию): <выход>/ACAD2018/<структура>/файл.dxf; "
            "flat: <выход>/<структура>/файл.ACAD2018.dxf"
        ),
    )
    parser.add_argument(
        "--engine",
        choices=("auto", "oda", "libredwg"),
        default="auto",
        help="движок конвертации (по умолчанию auto: сначала ODA, затем LibreDWG)",
    )
    parser.add_argument(
        "--oda-path",
        default=None,
        help="явный путь к ODAFileConverter.exe",
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help="включить аудит/починку чертежей в ODA (медленнее, но лечит битые DWG)",
    )
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=None,
        help="число параллельных процессов (по умолчанию 1 для ODA, 4 для LibreDWG)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        help="таймаут одной операции конвертации, секунды (по умолчанию 600)",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="не переделывать уже существующие DXF (докачка после обрыва)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="только показать план работ, ничего не конвертировать",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="не печатать прогресс по каждой папке",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    except (AttributeError, OSError):
        pass

    root = args.input.expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"Не папка: {root}")

    out_root = (
        args.output.expanduser().resolve()
        if args.output is not None
        else root.parent / f"{root.name}_dxf"
    )
    if out_root == root:
        raise SystemExit("Выходная папка совпадает со входной.")

    unknown = [v for v in args.versions if v not in ODA_VERSIONS]
    if unknown:
        raise SystemExit(
            f"Неизвестные версии DXF: {', '.join(unknown)}.\n"
            f"Доступно: {', '.join(ODA_VERSIONS)}"
        )
    versions = list(dict.fromkeys(args.versions))

    tasks, skipped = plan_tasks(
        root, out_root, versions, args.layout, args.skip_existing
    )
    total_files = sum(len(t.jobs) for t in tasks)
    source_dirs = len({t.src_dir for t in tasks})

    log(f"Вход:   {root}")
    log(f"Выход:  {out_root}")
    log(f"Версии: {', '.join(versions)}")
    log(
        f"Найдено: {source_dirs} папок с DWG, к конвертации {total_files} файлов"
        + (f", пропущено готовых {skipped}" if skipped else "")
    )

    if args.dry_run:
        for task in tasks:
            rel = task.src_dir.relative_to(root)
            log(f"  [{task.version}] {rel if str(rel) != '.' else '.'}: {len(task.jobs)} шт.")
        log("Сухой прогон: ничего не записано.")
        return 0

    if not tasks:
        log("Нечего конвертировать.")
        return 0

    engine = build_engine(args)
    jobs_count = args.jobs
    if jobs_count is None:
        # ODA File Converter плохо переносит несколько одновременных копий.
        jobs_count = 1 if isinstance(engine, OdaEngine) else 4
    jobs_count = max(1, jobs_count)

    log(f"Движок: {engine.name} ({engine.exe})")
    log(f"Потоков: {jobs_count}")
    log("")

    ok = 0
    failures: list[tuple[Path, str, str]] = []  # (dwg, версия, причина)
    done_tasks = 0
    started = time.monotonic()
    counter_lock = threading.Lock()

    def run(task: Task) -> tuple[Task, dict[str, str | None]]:
        return task, engine.convert_group(
            task.src_dir, task.jobs, task.version, args.timeout
        )

    with futures.ThreadPoolExecutor(max_workers=jobs_count) as pool:
        for task, results in pool.map(run, tasks):
            errors = [(name, err) for name, err in results.items() if err]
            with counter_lock:
                done_tasks += 1
                ok += len(results) - len(errors)
                for name, err in errors:
                    failures.append((task.src_dir / name, task.version, err))
                position = done_tasks
            if not args.quiet:
                rel = task.src_dir.relative_to(root)
                label = str(rel) if str(rel) != "." else "<корень>"
                status = f"ok {len(results) - len(errors)}"
                if errors:
                    status += f", ошибок {len(errors)}"
                log(f"[{position}/{len(tasks)}] {task.version} | {label}: {status}")

    prune_empty_dirs(out_root)

    elapsed = time.monotonic() - started
    log("")
    log(f"Готово за {elapsed:.1f} с: успешно {ok}, с ошибками {len(failures)}.")

    if failures:
        log("")
        log("Не сконвертировано:")
        for path, version, reason in failures[:50]:
            log(f"  [{version}] {path}: {reason}")
        if len(failures) > 50:
            log(f"  ... и ещё {len(failures) - 50}")
        return 1

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nПрервано пользователем.", file=sys.stderr)
        sys.exit(130)
