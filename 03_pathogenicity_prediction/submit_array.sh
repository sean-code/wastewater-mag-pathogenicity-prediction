#!/bin/bash
# submit_array.sh -- compute the number of size-balanced batches, then submit the
# parallel array + a dependent merge job. Run this from a login node (module load
# slurm first if needed). ONE command does the whole run.
set -eo pipefail

export NCBI_DIR="$HOME/datasets/04_ncbi_wastewater_metagenome_527639"
export MAGS_INPUT="$NCBI_DIR/mags_input"
export OUT_ROOT="$NCBI_DIR/pathogen_results_perorg"
export BATCH_MB=800          # <-- keep identical to run_pathogen_array.sbatch
export BATCH_MAX=400
CONCURRENCY=20               # how many array tasks run at once (raise if QOS allows)
BATCH_SCRIPT="$NCBI_DIR/batch_predict.py"

CONDA_BASE="$(conda info --base 2>/dev/null || echo "$HOME/miniconda3")"
source "$CONDA_BASE/etc/profile.d/conda.sh"
conda activate pathogen_ml

N=$(COUNT_ONLY=1 python "$BATCH_SCRIPT" | tail -1)
if ! [[ "$N" =~ ^[0-9]+$ ]] || [ "$N" -lt 1 ]; then
    echo "Could not compute batch count (got: '$N'). Is mags_input filtered/non-empty?"; exit 1
fi
G=$(ls "$MAGS_INPUT"/*.fna 2>/dev/null | wc -l)
echo "single-MAG genomes: $G  ->  $N size-balanced batches (~${BATCH_MB}MB each), up to $CONCURRENCY at once"

ARR=$(sbatch --parsable --array=1-${N}%${CONCURRENCY} "$HOME/run_pathogen_array.sbatch")
echo "array job:  $ARR"
MRG=$(sbatch --parsable --dependency=afterany:$ARR "$HOME/merge_results.sbatch")
echo "merge job:  $MRG  (runs after the array finishes)"
echo
echo "watch:  squeue -u $USER"
echo "        watch -n30 'ls $OUT_ROOT/batches/*/.done 2>/dev/null | wc -l'   # N of $N"
echo "        cat $OUT_ROOT/status.txt"
