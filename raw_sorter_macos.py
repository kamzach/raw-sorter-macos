#!/usr/bin/env python3
"""RAW Sorter macOS v3: rychle trideni EXIF -> YYYY/MM - mesic.

- Vychozi je nahlad (bez --apply).
- --apply --move: na stejnem filesystemu pouziva metadata-only rename (zadna kopie/hashing).
- --apply bez --move: kopiruje RAWy, originály ponechá.
- Je-li zdroj a cil na ruznych svazcich, --move skonci s vysvetlenim namisto
  pomaleho automatickeho kopirovani. Pouzij --apply pro kopirovani.
- Existujici cil NIKDY nema byt prepsan. Na macOS se pouzije renamex_np(RENAME_EXCL).
- Neprazdne XMP sidecary lze volitelne priradit k RAWu (--xmp).
- Nulove soubory se preskoci a vypisi do reportu, nemazou se.
- Po preruseni se da znovu spustit: uz presunute RAWy nejsou ve zdroji.

Poznamka: metadata-only move je mnohem rychlejsi, ale nenahrazuje zalohu ani
kontrolu integrity existujicich dat. Zkus nejprve na male slozce.
"""
from __future__ import annotations

import argparse
import csv
import ctypes
import errno
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

RAW_EXTENSIONS = {
    '.3fr', '.arw', '.cr2', '.cr3', '.crw', '.dcr', '.dng', '.erf',
    '.fff', '.iiq', '.k25', '.kdc', '.mef', '.mos', '.mrw', '.nef',
    '.nrw', '.orf', '.pef', '.raf', '.raw', '.rw2', '.rwl', '.sr2',
    '.srf', '.srw', '.x3f',
}
MONTHS = (
    '', 'leden', 'únor', 'březen', 'duben', 'květen', 'červen',
    'červenec', 'srpen', 'září', 'říjen', 'listopad', 'prosinec',
)
DATE_TAGS = (
    'DateTimeOriginal', 'SubSecDateTimeOriginal', 'CreateDate',
    'CreationDate', 'DateCreated', 'MediaCreateDate',
)
DATE_RE = re.compile(r'^\s*(\d{4})[:/-](\d{2})[:/-](\d{2})(?:\D|$)')


def inside(path: Path, folder: Path) -> bool:
    return path == folder or folder in path.parents


def scan(source: Path, dest: Path, with_xmp: bool):
    raws = []
    xmps = {}
    empty = []
    for folder, dirs, files in os.walk(source, followlinks=False):
        base = Path(folder)
        dirs[:] = sorted(d for d in dirs
                         if not (base / d).is_symlink() and not inside(base / d, dest))
        here_xmp = {}
        for name in sorted(files):
            p = base / name
            if name.startswith('._') or p.is_symlink() or not p.is_file():
                continue
            ext = p.suffix.lower()
            if ext in RAW_EXTENSIONS:
                try:
                    if p.stat().st_size == 0:
                        empty.append(p)
                    else:
                        raws.append(p)
                except OSError as exc:
                    print(f'Nelze zjistit velikost {p}: {exc}', file=sys.stderr)
            elif with_xmp and ext == '.xmp':
                here_xmp[name.casefold()] = p
        if with_xmp:
            xmps[base] = here_xmp
    return sorted(raws), xmps, empty


def extract_dates(files: list[Path]) -> dict[Path, dict]:
    results = {}
    total = len(files)
    batch_size = 100
    total_batches = (total + batch_size - 1) // batch_size
    started = time.monotonic()
    print(f'Načítám EXIF metadata: {total} RAWů / {total_batches} dávek.', flush=True)
    for offset in range(0, total, batch_size):
        batch = files[offset:offset + batch_size]
        cmd = ['exiftool', '-json', *('-' + tag for tag in DATE_TAGS),
               '--', *(str(p) for p in batch)]
        process = subprocess.run(cmd, capture_output=True, text=True,
                                 errors='replace', check=False)
        if process.stderr.strip():
            print('ExifTool:', process.stderr.strip()[:900], file=sys.stderr)
        try:
            records = json.loads(process.stdout)
        except json.JSONDecodeError as exc:
            print(f'Chyba parsování EXIF v dávce {offset + 1}: {exc}', file=sys.stderr)
            continue
        for record in records:
            name = record.get('SourceFile')
            if name:
                results[Path(name)] = record
        done = offset + len(batch)
        if done == total or done % 1000 == 0:
            elapsed = int(time.monotonic() - started)
            print(f'  EXIF: {done}/{total} ({100 * done / total:.1f} %) '
                  f'za {elapsed // 60} min {elapsed % 60} s', flush=True)
    return results


def date_from_exif(record: dict):
    for tag in DATE_TAGS:
        value = record.get(tag)
        if not isinstance(value, str):
            continue
        match = DATE_RE.match(value)
        if not match:
            continue
        try:
            taken = date(*(int(part) for part in match.groups()))
            if 1900 <= taken.year <= datetime.now().year + 2:
                return taken, tag
        except ValueError:
            pass
    return None, None


def sidecars_for(raw, xmps, used):
    lookup = xmps.get(raw.parent, {})
    found = []
    for name in (raw.name + '.xmp', raw.stem + '.xmp'):
        path = lookup.get(name.casefold())
        if path is not None and path not in used and path not in found:
            found.append(path)
    return found


def pairs_for(raw, target, sidecars):
    pairs = [(raw, target)]
    for sidecar in sidecars:
        if sidecar.name.casefold() == (raw.name + '.xmp').casefold():
            sidecar_name = target.name + sidecar.suffix
        else:
            sidecar_name = target.stem + sidecar.suffix
        pairs.append((sidecar, target.parent / sidecar_name))
    return pairs


def unique_pairs(raw, target, sidecars, reserved):
    """Kolize resi suffixem, bez drahych SHA kontrol existujicich souboru."""
    for index in range(1, 100000):
        candidate = target if index == 1 else target.with_name(
            f'{target.stem}_{index:03d}{target.suffix}')
        pairs = pairs_for(raw, candidate, sidecars)
        if any(t in reserved or t.exists() for _, t in pairs):
            continue
        reserved.update(t for _, t in pairs)
        return pairs, ('Přejmenováno kvůli kolizi' if index > 1 else '')
    raise OSError(f'Nelze najít volný název pro {target}')


# macOS renamex_np with RENAME_EXCL atomicky garantuje, ze neprepiseme cil.
_RENAME_EXCL = 0x00000004
_libc = None
if sys.platform == 'darwin':
    _libc = ctypes.CDLL(None, use_errno=True)
    _libc.renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    _libc.renamex_np.restype = ctypes.c_int


def rename_noreplace(src: Path, dst: Path):
    if dst.exists():
        raise FileExistsError(f'Cíl již existuje: {dst}')
    if _libc is not None:
        ret = _libc.renamex_np(os.fsencode(src), os.fsencode(dst), _RENAME_EXCL)
        if ret == 0:
            return
        code = ctypes.get_errno()
        # Napr. exFAT nemusi umet RENAME_EXCL. Rezervujeme si cil pomoci
        # exkluzivniho otevreni O_EXCL; normalni rename prepise JEN nasi prazdnou
        # rezervaci. Pro bezny jednouzivatelsky archiv je to bezpecny fallback.
        if code in (errno.ENOTSUP, errno.EOPNOTSUPP, errno.EINVAL):
            reserved_inode = None
            fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                reserved_inode = os.fstat(fd).st_ino
            finally:
                os.close(fd)
            try:
                os.rename(src, dst)
                return
            except BaseException:
                try:
                    stat = dst.stat()
                    if stat.st_ino == reserved_inode and stat.st_size == 0:
                        dst.unlink()
                except OSError:
                    pass
                raise
        raise OSError(code, os.strerror(code), str(dst))
    # Testovaci/non-macOS fallback (pozor: os.rename na POSIX muze prepsat cil).
    os.rename(src, dst)


def safe_copy(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    opened = False
    try:
        with src.open('rb') as in_file:
            with dst.open('xb') as out_file:
                opened = True
                shutil.copyfileobj(in_file, out_file, 4 * 1024 * 1024)
        shutil.copystat(src, dst)
    except BaseException:
        if opened:
            try:
                dst.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def perform_pairs(pairs, move):
    """V ramci jednoho disku rename, jinak kopie. Pri chybe rollback dokoncene skupiny."""
    done = []
    try:
        for src, dst in pairs:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not src.exists():
                raise FileNotFoundError(f'Zdroj zmizel: {src}')
            if move:
                if src.stat().st_dev != dst.parent.stat().st_dev:
                    raise OSError(f'Zdroj a cíl nejsou na stejném svazku: {src} -> {dst}')
                rename_noreplace(src, dst)
                done.append((src, dst, 'rename'))
            else:
                safe_copy(src, dst)
                done.append((src, dst, 'copy'))
    except BaseException:
        for src, dst, mode in reversed(done):
            try:
                if mode == 'rename':
                    rename_noreplace(dst, src)
                else:
                    dst.unlink(missing_ok=True)
            except OSError as rollback_exc:
                print(f'KRITICKÉ: Nepodařilo se vrátit {dst} -> {src}: '
                      f'{rollback_exc}', file=sys.stderr)
        raise


def preflight_move(source: Path, dest: Path, apply: bool) -> bool:
    """Porovnej zarizeni podle nejblizsi existujici slozky cile."""
    current = dest
    while not current.exists():
        if current == current.parent:
            raise FileNotFoundError(f'Cesta neexistuje: {dest}')
        current = current.parent
    same = source.stat().st_dev == current.stat().st_dev
    print('Svazky:', 'STEJNÝ – lze rychle přesouvat bez kopírování'
          if same else 'RŮZNÉ – přesun vyžaduje kopírování', flush=True)
    if apply and not same:
        raise RuntimeError('Přesun zastaven: zdroj a cíl jsou na různých svazcích. '
                           'Pro zachování originálů použij --apply bez --move.')
    return same


def main():
    parser = argparse.ArgumentParser(description='RAW Sorter v3: rok / měsíc, rychlý přesun na macOS')
    parser.add_argument('source', type=Path, help='Zdrojový adresář včetně podsložek')
    parser.add_argument('destination', type=Path, help='Cílový adresář')
    parser.add_argument('--apply', action='store_true', help='Provést změny; bez něj jen náhled')
    parser.add_argument('--move', action='store_true', help='Přesunout místo kopírovat (--apply)')
    parser.add_argument('--xmp', action='store_true', help='Přesunout i odpovídající XMP soubory')
    parser.add_argument('--mtime-fallback', action='store_true',
                        help='Použít datum modifikace, pokud není EXIF')
    parser.add_argument('--report', type=Path, help='CSV log výsledku')
    args = parser.parse_args()
    source = args.source.expanduser().resolve()
    dest = args.destination.expanduser().resolve()
    if not source.is_dir():
        parser.error(f'Zdrojová složka neexistuje: {source}')
    if inside(source, dest) or source == dest:
        parser.error('Cíl nesmí být stejný jako zdroj ani jeho předek.')
    if args.move and not args.apply:
        parser.error('--move vyžaduje --apply')
    if shutil.which('exiftool') is None:
        parser.error('Chybí ExifTool: brew install exiftool')
    if args.move:
        try:
            preflight_move(source, dest, args.apply)
        except (RuntimeError, OSError) as exc:
            parser.error(str(exc))
    raws, xmps, empties = scan(source, dest, args.xmp)
    print(f'Zdrojových RAWů: {len(raws) + len(empties)}; '
          f'prázdných (přeskočeno): {len(empties)}; zbývá: {len(raws)}', flush=True)
    if not raws:
        if empties:
            print('UPOZORNĚNÍ: Ve zdroji jsou prázdné RAW soubory.', file=sys.stderr)
        return 0
    metadata = extract_dates(raws)
    used_xmp = set()
    reserved = set()
    groups = []
    unknown = 0
    collisions = 0
    unreadable = []
    for raw in raws:
        record = metadata.get(raw)
        if record is None or record.get('Error'):
            unreadable.append(raw)
            continue
        taken, tag = date_from_exif(record)
        if taken is None and args.mtime_fallback:
            try:
                taken = datetime.fromtimestamp(raw.stat().st_mtime).date()
                tag = 'FileModifyDate (fallback)'
            except OSError:
                pass
        if taken:
            folder = dest / str(taken.year) / f'{taken.month:02d} - {MONTHS[taken.month]}'
        else:
            folder = dest / '_Neurcene_datum'
            tag = 'Bez data pořízení'
            unknown += 1
        sidecars = sidecars_for(raw, xmps, used_xmp) if args.xmp else []
        pairs, note = unique_pairs(raw, folder / raw.name, sidecars, reserved)
        used_xmp.update(sidecars)
        if note:
            collisions += 1
        groups.append((pairs, tag, note))
    print(f'Připraveno: {len(groups)} RAWů; bez data: {unknown}; '
          f'kolize názvů: {collisions}; prázdných: {len(empties)}; '
          f'chyba čtení metadat: {len(unreadable)}', flush=True)
    print('Režim:', ('PŘESUN NA STEJNÉM SVAZKU (rychlý rename)' if args.move else
                  'KOPÍROVÁNÍ' if args.apply else 'NÁHLED – nic neměním'), flush=True)
    for pairs, _, _ in groups[:10]:
        print(f'  {pairs[0][0]} -> {pairs[0][1]}')
    if len(groups) > 10:
        print(f'  ... a dalších {len(groups) - 10} RAWů')
    log = [(str(path), '', 'PŘESKOČENO', 'Prázdný RAW (0 B)') for path in empties]
    log.extend((str(path), '', 'PŘESKOČENO', 'ExifTool nevrátil čitelná metadata')
               for path in unreadable)
    failed = 0
    started = time.monotonic()
    try:
        for number, (pairs, tag, note) in enumerate(groups, 1):
            status = 'NÁHLED'
            if args.apply:
                try:
                    perform_pairs(pairs, args.move)
                    status = 'PŘESUNUTO' if args.move else 'ZKOPÍROVÁNO'
                except OSError as exc:
                    failed += 1
                    status = 'CHYBA'
                    note = (note + '; ' + str(exc)).strip('; ')
                    print(f'CHYBA {pairs[0][0]}: {exc}', file=sys.stderr)
            for src, target in pairs:
                log.append((str(src), str(target), status, (tag + '; ' + note).strip('; ')))
            if args.apply and (number == 1 or number % 200 == 0 or number == len(groups)):
                sec = int(time.monotonic() - started)
                print(f'  Přesunuto/zpracováno {number}/{len(groups)} '
                      f'({100 * number / len(groups):.1f} %), '
                      f'uplynulo {sec // 60} min {sec % 60} s', flush=True)
    except KeyboardInterrupt:
        print('\nPřerušeno: již přesunuté soubory zůstaly v cíli. '
              'Skript lze bezpečně spustit znovu.', file=sys.stderr)
        failed += 1
    finally:
        if args.report:
            report_path = args.report.expanduser().resolve()
            report_path.parent.mkdir(parents=True, exist_ok=True)
            with report_path.open('w', encoding='utf-8-sig', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(('Zdroj', 'Cíl', 'Stav', 'Poznámka'))
                writer.writerows(log)
            print('CSV report:', report_path, flush=True)
    if args.apply:
        ok = sum(1 for entry in log if entry[2] == ('PŘESUNUTO' if args.move else 'ZKOPÍROVÁNO'))
        print(f'Dokončeno/zastaveno: úspěšně {ok} položek (RAW + XMP), '
              f'chybných RAW skupin {failed}.', flush=True)
    else:
        print('NÁHLED HOTOV – skutečné přesouvání: --apply --move', flush=True)
    return 1 if failed else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('\nPřerušeno uživatelem.', file=sys.stderr)
        sys.exit(130)