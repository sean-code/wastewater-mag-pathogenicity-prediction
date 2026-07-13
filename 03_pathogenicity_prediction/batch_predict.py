#!/usr/bin/env python3
"""
batch_predict.py -- run the REAL `pathogen_predict.py mags` over a large FASTA
folder in fixed-size batches, resumably, then merge results.

What it does (all inside ONE Slurm job):
  1. Lists mags_input/*.fna in a fixed sorted order.
  2. Slices them into consecutive batches of BATCH_SIZE (default 200); the last
     batch is the remainder. Batches are DISJOINT -> no genome runs twice.
  3. For each batch not yet marked .done: builds an input dir of symlinks, runs
        python pathogen_predict.py --threads T --model-dir <model> mags \
               --input <batch/input> --output <batch/output>
     (the exact command you run by hand), logs it, and on success writes a
     .done sentinel.
  4. Resumable: rerun/resubmit and it SKIPS finished batches and continues.
  5. Merges every batch prediction_results.csv into prediction_results_ALL.csv
     (single header) and the JSONs into prediction_results_ALL.json.

Config via env vars (the sbatch sets them); sensible defaults otherwise.
"""
import os, sys, csv, json, time, shutil, subprocess, logging
from pathlib import Path
from datetime import datetime

# --------------------------- config ---------------------------
NCBI_DIR   = Path(os.environ.get("NCBI_DIR",
                  str(Path("~/datasets/04_ncbi_wastewater_metagenome_527639").expanduser())))
PIPE       = Path(os.environ.get("PIPE_DIR",
                  str(Path("~/pathogen-projects/complete-chromosome-pathogen-non-pathogen-genomes").expanduser())))
INPUT_DIR  = Path(os.environ.get("MAGS_INPUT", str(NCBI_DIR / "mags_input")))
OUT_ROOT   = Path(os.environ.get("OUT_ROOT",  str(NCBI_DIR / "pathogen_results")))
MODEL_DIR  = Path(os.environ.get("MODEL_DIR", str(PIPE / "saved_model")))
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "200"))            # legacy (unused when size-balancing)
BATCH_MB   = float(os.environ.get("BATCH_MB", "800"))           # target total MB per size-balanced batch
BATCH_MAX  = int(os.environ.get("BATCH_MAX", "400"))            # hard cap on files per batch
THREADS    = int(os.environ.get("THREADS", os.environ.get("SLURM_CPUS_PER_TASK", "8")))

BATCH_DIR   = OUT_ROOT / "batches"
MASTER_CSV  = OUT_ROOT / "prediction_results_ALL.csv"
MASTER_JSON = OUT_ROOT / "prediction_results_ALL.json"
STATUS      = OUT_ROOT / "status.txt"
OUT_ROOT.mkdir(parents=True, exist_ok=True)
BATCH_DIR.mkdir(parents=True, exist_ok=True)

# --------------------------- logging ---------------------------
log = logging.getLogger("batch"); log.setLevel(logging.INFO)
if not log.handlers:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    sh = logging.StreamHandler(sys.stdout); sh.setFormatter(fmt); log.addHandler(sh)
    fh = logging.FileHandler(OUT_ROOT / "batch_master.log"); fh.setFormatter(fmt); log.addHandler(fh)

def status(msg):
    try:
        STATUS.write_text(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}\n")
    except OSError:
        pass

# --------------------------- run ---------------------------
def run_batch(bi, nb, chunk):
    bdir = BATCH_DIR / f"batch_{bi+1:04d}"
    done_flag = bdir / ".done"
    if done_flag.exists():
        log.info("batch %04d/%d: already done (%d genomes) -> skip", bi + 1, nb, len(chunk))
        return "skip"

    indir  = bdir / "input"
    outdir = bdir / "output"
    btmp   = bdir / "tmp"
    indir.mkdir(parents=True, exist_ok=True)
    # output/tmp hold real files from any prior crash -> safe to wipe and recreate
    for d in (outdir, btmp):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
    # input: this batch's genome set is fixed, so just ensure the symlinks exist
    # (never rmtree a dir of symlinks -- that can fail on network/overlay FS)
    for f in chunk:
        link = indir / f.name
        if not link.is_symlink():
            link.symlink_to(f.resolve())              # symlink = no data duplication

    env = dict(os.environ); env["TMPDIR"] = str(btmp)  # isolate temp per batch
    cmd = [sys.executable, "pathogen_predict.py",
           "--threads", str(THREADS), "--model-dir", str(MODEL_DIR),
           "mags", "--input", str(indir), "--output", str(outdir)]

    status(f"running batch {bi+1}/{nb} ({len(chunk)} genomes)")
    log.info("batch %04d/%d: running %d genomes -> %s", bi + 1, nb, len(chunk), outdir)
    t0 = time.time()
    with open(bdir / "batch.log", "ab") as blog:
        blog.write(f"\n=== batch {bi+1}/{nb}  {datetime.now()}  cmd: {' '.join(cmd)} ===\n".encode())
        blog.flush()
        rc = subprocess.run(cmd, cwd=str(PIPE), env=env,
                            stdout=blog, stderr=subprocess.STDOUT).returncode
    dt = time.time() - t0

    csv_out = outdir / "prediction_results.csv"
    if rc == 0 and csv_out.exists():
        done_flag.write_text(f"{datetime.now():%Y-%m-%d %H:%M:%S} ok {len(chunk)} genomes {dt:.0f}s\n")
        log.info("batch %04d/%d: DONE in %.0fs", bi + 1, nb, dt)
        return "ok"
    log.error("batch %04d/%d: FAILED (rc=%s, csv=%s) -> %s",
              bi + 1, nb, rc, csv_out.exists(), bdir / "batch.log")
    return "fail"

def merge():
    csvs = sorted(BATCH_DIR.glob("batch_*/output/prediction_results.csv"))
    header, rows = None, 0
    with open(MASTER_CSV, "w", newline="") as out:
        w = None
        for c in csvs:
            with open(c, newline="") as f:
                r = csv.reader(f)
                try:
                    h = next(r)
                except StopIteration:
                    continue
                if header is None:
                    header = h; w = csv.writer(out); w.writerow(header)
                for row in r:
                    if row:
                        w.writerow(row); rows += 1
    log.info("merge: %d batch CSVs -> %d rows -> %s", len(csvs), rows, MASTER_CSV)

    merged = []
    for j in sorted(BATCH_DIR.glob("batch_*/output/prediction_results.json")):
        try:
            data = json.loads(j.read_text())
        except Exception:
            continue
        merged.extend(data) if isinstance(data, list) else merged.append(data)
    if merged:
        try:
            MASTER_JSON.write_text(json.dumps(merged, indent=1))
            log.info("merge: %d JSON records -> %s", len(merged), MASTER_JSON)
        except OSError as e:
            log.warning("JSON merge skipped: %s", e)
    return rows

def build_batches(fna):
    """Pack files into SIZE-BALANCED batches: each batch <= BATCH_MB total (and
    <= BATCH_MAX files). Input is name-sorted, so this is deterministic -- every
    array task computes the identical batches. Balancing by bytes keeps runtime
    even so a few larger genomes don't stall one batch."""
    cap = BATCH_MB * 1_000_000
    batches, cur, cursz = [], [], 0
    for f in fna:
        try:
            sz = f.stat().st_size
        except OSError:
            sz = 0
        if cur and (cursz + sz > cap or len(cur) >= BATCH_MAX):
            batches.append(cur); cur, cursz = [], 0
        cur.append(f); cursz += sz
    if cur:
        batches.append(cur)
    return batches

def main():
    # Modes (env-controlled, for Slurm job arrays):
    #   COUNT_ONLY=1        -> print the number of size-balanced batches, exit (for --array sizing)
    #   MERGE_ONLY=1        -> only merge existing batch results, then exit
    #   ONLY_BATCH=<1..NB>  -> run exactly one batch (array task), no merge
    #   (none)              -> run all batches sequentially, then merge
    MERGE_ONLY = os.environ.get("MERGE_ONLY") == "1"
    COUNT_ONLY = os.environ.get("COUNT_ONLY") == "1"
    ONLY_BATCH = os.environ.get("ONLY_BATCH")

    if MERGE_ONLY:
        rows = merge()
        msg = f"MERGE ONLY: {rows} predictions -> {MASTER_CSV.name}"
        status(msg); log.info(msg)
        return

    fna = sorted(INPUT_DIR.glob("*.fna"))
    if COUNT_ONLY:
        print(len(build_batches(fna)))                # ONLY an integer on stdout, for --array
        return
    assert fna, f"No .fna files in {INPUT_DIR}"
    assert (PIPE / "pathogen_predict.py").exists(), f"pathogen_predict.py not in {PIPE}"
    assert MODEL_DIR.exists(), f"model dir missing: {MODEL_DIR}"
    batches = build_batches(fna)
    nb = len(batches)

    if ONLY_BATCH:
        bi = int(ONLY_BATCH) - 1                       # array IDs are 1-based
        assert 0 <= bi < nb, f"ONLY_BATCH {ONLY_BATCH} out of range 1..{nb}"
        chunk = batches[bi]
        mb = sum((f.stat().st_size for f in chunk), 0) / 1e6
        log.info("ARRAY TASK: batch %d/%d (%d genomes, %.0f MB) | threads=%d", bi + 1, nb, len(chunk), mb, THREADS)
        r = run_batch(bi, nb, chunk)
        log.info("batch %d/%d -> %s", bi + 1, nb, r)
        sys.exit(0 if r in ("ok", "skip") else 1)      # nonzero so a failed array task is visible

    # ---- sequential mode ----
    log.info("START: %d genomes | %d size-balanced batches (~%.0f MB each) | threads=%d | out=%s",
             len(fna), nb, BATCH_MB, THREADS, OUT_ROOT)
    counts = {"ok": 0, "skip": 0, "fail": 0}
    for bi, chunk in enumerate(batches):
        counts[run_batch(bi, nb, chunk)] += 1
        done = counts["ok"] + counts["skip"]
        status(f"progress {done}/{nb} batches (ok={counts['ok']} skip={counts['skip']} fail={counts['fail']})")
    rows = merge()
    msg = (f"ALL DONE: {counts['ok']} ran, {counts['skip']} already-done, "
           f"{counts['fail']} failed | {rows} predictions -> {MASTER_CSV.name}")
    status(msg); log.info(msg)
    if counts["fail"]:
        log.warning("Some batches failed — resubmit the same job to retry only those (they lack .done).")

if __name__ == "__main__":
    main()
