# Wastewater MAG Pathogenicity Inference — Findings Report

**Artifact analyzed:** `prediction_results_ALL.csv` (merged output of the distributed `pathogen_predict.py mags` run)
**Records:** 5,328 genomes · **Schema:** 60 columns · **Primary key:** `Sample` (5,328 distinct — 0 duplicates)
**Framing:** computer science / data engineering

---

## 1. Executive summary

The distributed inference pipeline executed correctly end-to-end: 5,328 single-organism assemblies were fanned out across a Slurm job array, each processed by the unchanged `pathogen_predict.py mags` inference tool, checkpointed per batch, and reduced into one keyed dataset with no duplicate or missing keys. **As a data pipeline, the run is a success.**

The **model output**, however, should not be read at face value. The headline distribution is 98.0% `PATHOGEN`, but that figure is dominated by an **asymmetric fallback path** and **out-of-distribution inputs**, not by strong positive evidence. The trustworthy signal lives in a stratified high-confidence subset (~1,453 genomes) plus the ML-derived negatives (89 genomes). Additionally, the merged CSV has a **schema-integrity defect** (field misalignment in ~86% of rows) that makes the numeric BLAST columns unreliable in aggregate and must be repaired before any quantitative use.

---

## 2. Pipeline architecture & execution (data engineering)

The workload is an **embarrassingly-parallel batch-inference job** expressed as a scatter/gather:

- **Partitioning:** input FASTA set → deterministic, name-sorted, **size-balanced shards** (~800 MB each) so wall-time per shard is even and no single large genome starves a shard.
- **Scatter:** one Slurm **array task per shard** (`ONLY_BATCH=$SLURM_ARRAY_TASK_ID`), ~20 concurrent across `compute[001–009]`. Idempotent: each shard writes a `.done` sentinel, so a task timeout/failure is retried on resubmit without reprocessing completed shards (exactly-once semantics at shard granularity).
- **Isolation:** per-shard `TMPDIR` and output directory → no write contention; the model/pangenome/BLAST DB are read-only shared inputs (safe concurrent reads).
- **Gather:** a dependency-gated (`afterany`) merge task concatenates per-shard `prediction_results.csv` under a single header and unions the per-shard JSON.

**Integrity checks passed:** `|distinct(Sample)| == |rows| == 5,328` (no key collisions across shards, i.e., the partitions were truly disjoint). This validates the scatter/gather correctness.

---

## 3. Dataset & result distribution

| Field | Value |
|---|---|
| Genomes scored | 5,328 |
| `Final_Prediction = PATHOGEN` | 5,224 (98.0%) |
| `Final_Prediction = NON-PATHOGEN` | 89 (1.7%) |
| `Final_Prediction = UNKNOWN` | 15 (0.3%) |
| ML branch available | 2,303 (43.2%) |
| BLAST-only fallback | 3,025 (56.8%) |
| `Gene_Families_Found` (of 705) | median 17, mean 37, max 485 |
| Genomes below ML threshold (<35 families) | 3,937 (73.9%) |
| `ML_Probability` (where present) | median 0.935, mean 0.876 |

**Decision-path breakdown (the key table):**

| Path | n | PATHOGEN | NON-PATHOGEN | UNKNOWN |
|---|---|---|---|---|
| ML-supported (feature overlap sufficient) | 2,303 | 96% | 4% | — |
| BLAST-only fallback (ML unavailable) | 3,025 | **100%** (3,010) | 0% | 15 |

---

## 4. Critical interpretation — why 98% is not a finding

Three structural effects, not biology, produce the near-universal `PATHOGEN` label:

1. **Out-of-distribution inputs.** The classifier's features are gene-family presence/absence against a pangenome built from *known pathogens*. These wastewater MAGs are environmental organisms with **low overlap** to that feature space — median 17 of 705 families. Consequently the ML branch could only run on **43%** of genomes; the other 57% fell through to BLAST-only. In ML terms, most inputs sit far outside the training manifold, so the model abstains and control passes to the fallback.

2. **Asymmetric fallback (label/base-rate bias).** The BLAST fallback matches against a **pathogen-only reference database**. By construction its nearest neighbor is always *some pathogen*, so the fallback path is **structurally incapable of emitting NON-PATHOGEN** — and indeed it returns `PATHOGEN` for 100% of the 3,010 genomes it decided. This is a class-prior artifact: the decision rule cannot express the negative class. These 3,010 calls should be read as "screened, inconclusive," not as positives.

3. **Divergent-match evidence.** The dominant evidence tier is *"MEDIUM — BLAST (LOW (divergent strain)), ML unavailable"* (54% of rows). The matches are low-identity ("divergent"), i.e., nearest-reference rather than species-level identification.

**Defensible signal.** Stratifying to the ML-supported, calibrated tier yields **1,453 genomes with `ML_Probability ≥ 0.90` (all PATHOGEN)** — this is the high-confidence positive set. The **89 ML-derived NON-PATHOGEN** calls are also meaningful (the negative class only appears where ML actually ran). Report findings from these tiers; treat the BLAST-only 57% as an inconclusive screen.

**Taxonomic neighbors (indicative).** Recurring nearest-reference taxa are *Serratia marcescens*, *Aeromonas* spp., *Plesiomonas shigelloides*, and the *Bacillus cereus* group — waterborne/enteric organisms that are biologically plausible neighbors for sewage MAGs. High-alarm hits such as *Bacillus anthracis* are almost certainly low-identity cross-matches within the *B. cereus* group, not detections. (Exact per-taxon counts are affected by the schema defect in §5.)

---

## 5. Data-quality defect (must fix before quantitative BLAST analysis)

Positional parsing against the 60-column header reveals **field misalignment in 4,568 rows (85.7%)**: the `Verification_Status` column holds organism names instead of status tokens, and 44 `BLAST_*_BestIdent_%` values fall outside `[0,100]` (median of the column parses to ~3, max ~823). Root cause is almost certainly **inconsistent CSV quoting of comma-bearing fields** (e.g., `Evidence_Level = "…(divergent strain), ML unavailable"`) across shards, producing ragged rows that shift subsequent columns.

**Impact:** the ensemble/decision columns (`Final_Prediction`, `ML_Probability`, `Gene_Families_Found`) are reliable, but the **numeric BLAST columns cannot be trusted in aggregate** until the file is re-emitted or re-parsed.

**Remediation options:** (a) re-derive the table from the per-shard `prediction_results.json` (structured, not delimiter-sensitive); or (b) re-emit with a strict quote-all CSV writer and add a **schema contract** (fixed column count + type/range assertions, pandera/Great-Expectations style) to the merge step so ragged rows fail fast.

---

## 6. Recommendations

1. **Repair the schema** (re-parse from JSON or quote-all re-emit) and add a validation gate to `merge()`; reject/quarantine rows whose field count ≠ 60.
2. **Stratify, don't aggregate.** Publish the ML-available tier (esp. `ML_Probability ≥ 0.90`, n=1,453) and the ML negatives (n=89); label the BLAST-only 3,025 as "inconclusive / OOD screen," not positives.
3. **Fix the asymmetric fallback.** Either BLAST against a **balanced** reference (pathogen + non-pathogen) or map the pathogen-only fallback to `UNKNOWN` rather than `PATHOGEN`. This is the single change with the largest effect on result validity.
4. **Make OOD explicit.** Genomes with feature overlap below threshold should be emitted as `UNKNOWN / not-classifiable` with a reason code, rather than routed to a fallback that can only say "pathogen."
5. **Metagenome assemblies (set aside earlier).** They remain unprocessed by design (per-organism model requires single genomes). If raw reads become available, bin them (metaBAT2/coverage) into MAGs and re-run through this same array.

---

## 7. Reproducibility & provenance

- Deterministic partitioning (name-sorted, size-cap) ⇒ every array task computes identical shards ⇒ rerun-stable.
- Per-shard `.done` sentinels + `afterany` merge ⇒ resumable, exactly-once at shard granularity.
- Inputs: single-MAG subset of NCBI taxon 527639 (wastewater metagenome, 2024–2026); model = committed `saved_model/` (RF + XGBoost) over the ppanggolin combined pangenome (`gene_presence_absence.Rtab`).
- Output keyed by assembly accession (`GCA_*`); 5,328 records, 0 duplicate keys.
