#!/usr/bin/env python3
"""
filter_mags.py -- keep only single-organism-sized genomes in mags_input/ for the
per-organism `mags` prediction. Moves oversized (whole-metagenome assemblies) and
tiny (fragmentary) FASTA OUT of mags_input into sibling folders, leaving only
single-MAG-sized files. Size-based (instant, uses file size ~= genome length).
Idempotent: rerun any time. Writes a size profile TSV for your PI.

    python filter_mags.py
    # tune with env: MIN_MB (default 0.5), MAX_MB (default 12)
"""
import os, shutil, statistics
from pathlib import Path
from collections import Counter

NCBI_DIR = Path(os.environ.get("NCBI_DIR",
                str(Path("~/datasets/04_ncbi_wastewater_metagenome_527639").expanduser())))
MAGS = NCBI_DIR / "mags_input"
META = NCBI_DIR / "metagenome_assemblies"   # set aside: multi-organism community assemblies
TINY = NCBI_DIR / "tiny_fragments"          # set aside: < MIN_MB, too small to be one genome
MIN_MB = float(os.environ.get("MIN_MB", "0.5"))
MAX_MB = float(os.environ.get("MAX_MB", "12"))
META.mkdir(exist_ok=True); TINY.mkdir(exist_ok=True)

files = sorted(MAGS.glob("*.fna"))
rows, moved = [], Counter()
for f in files:
    mb = f.stat().st_size / 1e6
    cls = "metagenome" if mb > MAX_MB else ("tiny" if mb < MIN_MB else "single_MAG")
    rows.append((f.name, mb, cls))
    if cls == "metagenome":
        shutil.move(str(f), str(META / f.name)); moved["metagenome"] += 1
    elif cls == "tiny":
        shutil.move(str(f), str(TINY / f.name)); moved["tiny"] += 1

kept = len(list(MAGS.glob("*.fna")))
print(f"scanned {len(files)} files in {MAGS}")
print(f"  kept  single-MAG ({MIN_MB}-{MAX_MB} MB): {kept}")
print(f"  moved metagenome (>{MAX_MB} MB)        : {moved['metagenome']:>5}  -> {META.name}/")
print(f"  moved tiny       (<{MIN_MB} MB)         : {moved['tiny']:>5}  -> {TINY.name}/")
if rows:
    mbs = [mb for _, mb, _ in rows]
    print(f"  size MB: min={min(mbs):.2f}  median={statistics.median(mbs):.2f}  max={max(mbs):.2f}")

prof = NCBI_DIR / "mags_size_profile.tsv"
with open(prof, "w") as o:
    o.write("file\tsize_MB\tclass\n")
    for n, mb, cls in sorted(rows, key=lambda x: -x[1]):
        o.write(f"{n}\t{mb:.2f}\t{cls}\n")
print("profile ->", prof, "(sorted largest-first)")
