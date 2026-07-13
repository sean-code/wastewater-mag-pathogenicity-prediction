#!/usr/bin/env python3
"""
restore_ncbi_mags.py -- rebuild the full NCBI wastewater-metagenome MAG set as
FASTA in mags_input/, WITHOUT ever writing a .zip to disk.

It re-queries NCBI for taxon 527639 (release years 2024-2026), skips accessions
you already have as mags_input/<acc>.fna (the 1,143 that survived), downloads each
missing genome into memory, extracts the .fna, and moves on. Resumable and
idempotent: rerun any time; it only fetches what's missing. Space footprint is
FASTA only (no zip doubling).

RUN (in a terminal so you can watch it):
    python restore_ncbi_mags.py
    # optional, raises NCBI rate limit:  export NCBI_API_KEY=xxxx  before running
    # in a second terminal, watch progress:
    #   watch -n5 'ls ~/datasets/04_ncbi_wastewater_metagenome_527639/mags_input | wc -l'
"""
import os, sys, json, time, io, zipfile, shutil, urllib.request, urllib.error
from pathlib import Path
from collections import Counter

BASE_DIR  = Path(os.environ.get("MAG_BASE_DIR", "~/datasets")).expanduser()
NCBI_DIR  = BASE_DIR / "04_ncbi_wastewater_metagenome_527639"
FASTA_DIR = NCBI_DIR / "mags_input"
TAXON     = 527639
YEARS     = (2024, 2026)
API_KEY   = os.environ.get("NCBI_API_KEY")           # optional
V2 = "https://api.ncbi.nlm.nih.gov/datasets/v2"
UA = {"User-Agent": "restore-mags/1.0 (mailto:ngangajohn536@gmail.com)"}
FASTA_DIR.mkdir(parents=True, exist_ok=True)

def get(url, tries=6):
    if API_KEY:
        url += ("&" if "?" in url else "?") + "api_key=" + API_KEY
    for a in range(1, tries + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503):
                time.sleep(min(2 ** a, 30)); continue
            raise
        except Exception:
            time.sleep(min(2 ** a, 30))
    raise RuntimeError("GET failed: " + url)

def collect_accessions():
    accs, token, page = [], None, 0
    while True:
        url = f"{V2}/genome/taxon/{TAXON}/dataset_report?page_size=1000&filters.assembly_source=all"
        if token:
            url += f"&page_token={token}"
        js = json.loads(get(url).decode())
        for r in js.get("reports", []):
            d = r.get("assembly_info", {}).get("release_date", "")
            try:
                y = int(d[:4])
            except ValueError:
                y = None
            if y and YEARS[0] <= y <= YEARS[1]:
                accs.append(r["accession"])
        page += 1
        token = js.get("next_page_token")
        print(f"  report page {page}: {len(accs)} accessions in range", flush=True)
        if not token:
            break
    return accs

def fetch_one(acc):
    dest = FASTA_DIR / f"{acc}.fna"
    if dest.exists() and dest.stat().st_size > 0:
        return "skip"
    url = f"{V2}/genome/accession/{acc}/download?include_annotation_type=GENOME_FASTA"
    try:
        data = get(url)
    except Exception as e:
        return f"dlfail:{e}"
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:               # zip lives in RAM only
            fna = [m for m in z.namelist() if m.endswith("_genomic.fna")] or \
                  [m for m in z.namelist() if m.endswith(".fna")]
            if not fna:
                return "no_fna"
            tmp = dest.with_suffix(".fna.part")
            with z.open(fna[0]) as s, open(tmp, "wb") as o:
                shutil.copyfileobj(s, o)
        with open(tmp, "rb") as f:
            head = f.read(1)
        if tmp.stat().st_size == 0 or head != b">":
            tmp.unlink(missing_ok=True); return "bad_fna"
        tmp.replace(dest)
        return "ok"
    except zipfile.BadZipFile:
        return "bad_zip"
    except OSError as e:
        return f"OSERROR:{e}"

def main():
    print("Collecting accession list from NCBI (taxon %d, %d-%d)…" % (TAXON, *YEARS), flush=True)
    accs = collect_accessions()
    have = {p.stem for p in FASTA_DIR.glob("*.fna")}
    todo = [a for a in accs if a not in have]
    print(f"total in range: {len(accs)} | already have: {len(have)} | to fetch: {len(todo)}", flush=True)
    c = Counter()
    for i, acc in enumerate(todo, 1):
        r = fetch_one(acc); c[r.split(':')[0]] += 1
        if r.startswith("OSERROR"):
            print(f"STOPPED at {acc}: {r}\n-> free space (delete 07_nature) or get scratch, then rerun.", flush=True)
            break
        if i % 50 == 0 or i == len(todo):
            print(f"  {i}/{len(todo)}  {dict(c)}  fna_total={len(list(FASTA_DIR.glob('*.fna')))}", flush=True)
        time.sleep(0.15)                                            # polite pacing
    print("DONE:", dict(c), "| FASTA total:", len(list(FASTA_DIR.glob('*.fna'))), flush=True)

if __name__ == "__main__":
    main()
