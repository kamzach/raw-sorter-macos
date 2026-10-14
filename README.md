# RAW Sorter for macOS

Jednoduchý nástroj pro třídění RAW fotografií podle data pořízení (EXIF) do složek `ROK/MM - měsíc`. Rekurzivně prochází zdrojové podsložky a podporuje související soubory `.xmp`.

**Verze:** 3 (Fast Move)  
**Požadavky:** macOS, Python 3.10+ a [ExifTool](https://exiftool.org/).  
**Doporučení:** před hromadným přesunem mít nezávislou zálohu fotografií.

## Proč je Fast Move rychlý

Při použití `--apply --move` na **stejném souborovém svazku** se používá operace přejmenování/přesunu (`rename`) místo kopírování a opakovaného čtení celých RAW souborů. Na různých svazcích skript z bezpečnostních důvodů přesun odmítne; pro kopírování se používá `--apply` bez `--move`.

## Instalace na macOS

1. Pokud nemáš Homebrew, postupuj podle [brew.sh](https://brew.sh/).
2. Nainstaluj ExifTool:

   ```bash
   brew install exiftool
   ```

3. Ověř dostupnost Pythonu:

   ```bash
   python3 --version
   ```

Nástroj využívá jen standardní knihovnu Pythonu, není potřeba instalovat balíčky přes `pip`.

## Použití

Nejdříve vyzkoušej pouze **náhled bez změn**:

```bash
python3 raw_sorter_macos.py "/cesta/ke/zdroji" "/cesta/k/cili" --xmp
```

Pro rychlý přesun RAWů a XMP na stejném svazku:

```bash
python3 raw_sorter_macos.py "/cesta/ke/zdroji" "/cesta/k/cili" --xmp --apply --move --report "$HOME/Desktop/raw_sorter_report.csv"
```

Pro **kopírování** s ponecháním originálů:

```bash
python3 raw_sorter_macos.py "/cesta/ke/zdroji" "/cesta/k/cili" --xmp --apply
```

Pro výpis všech parametrů:

```bash
python3 raw_sorter_macos.py --help
```

### Příklad výsledku

```text
RAW_Archiv/
├── 2024/
│   ├── 01 - leden/
│   │   ├── L1000101.DNG
│   │   └── L1000101.XMP
│   └── 07 - červenec/
├── 2025/
│   └── 10 - říjen/
└── _Neurcene_datum/
```

## Parametry

| Parametr | Funkce |
| --- | --- |
| `source` | Zdrojová složka včetně podsložek |
| `destination` | Cílový archiv |
| `--apply` | Provede změny; bez něj pouze náhled |
| `--move` | Přesouvá místo kopírování (vyžaduje `--apply`) |
| `--xmp` | Zahrne odpovídající XMP sidecary |
| `--mtime-fallback` | Když nelze zjistit EXIF datum, použije čas poslední modifikace souboru |
| `--report SOUBOR.csv` | Zapíše CSV přehled operací a chyb |

## Bezpečnost a známá omezení

- Datum se primárně čte z metadat fotografie, nikoliv z názvu složky.
- RAWy s nulovou velikostí nebo nečitelnými EXIF daty skript přeskočí. Fotografie bez rozpoznaného data (ale s čitelnými metadaty) ukládá do `_Neurcene_datum`, pokud není zapnuto `--mtime-fallback`.
- Při kolizi názvů přidá číselný suffix místo přepsání existujícího souboru.
- Operace `--move` mezi různými svazky není podporována; použij kopírování `--apply`.
- CSV report zaznamenává **aktuální běh**. Nelze jím zpětně vytvořit původní seznam přesunů z běhu, při kterém `--report` nebyl zapnutý.
- Přerušení a nové spuštění je možné, protože úspěšně přesunuté soubory již nejsou ve zdroji. Skript nemá transakční historii pro automatický návrat celé akce.
- Na některých souborových systémech nemusí být dostupný atomický přesun s `RENAME_EXCL`; skript proto používá náhradní mechanismus. Nesdílej současně cílovou složku s dalším procesem, který ji aktivně přejmenovává nebo přesouvá.

## Struktura projektu

```text
raw-sorter-macos/
├── raw_sorter_macos.py
├── README.md
├── .gitignore
└── tests/
    └── test_raw_sorter.py
```

## Testy

```bash
python3 -m unittest discover -s tests -v
```

## Licence

Zatím není zvolena. Pro veřejné použití je vhodné rozhodnout o licenci (např. MIT) před zveřejněním.