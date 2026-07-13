#!/bin/bash
#SBATCH --job-name=pathogen_array
#SBATCH --output=/mmfs2/home/usd.local/john.kangethe/logs/pb_%A_%a.out
#SBATCH --error=/mmfs2/home/usd.local/john.kangethe/logs/pb_%A_%a.err
#SBATCH --time=2-00:00:00            # per TASK (each runs one ~800MB batch); generous
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --partition=compute
#SBATCH --array=1-1                  # OVERRIDDEN by submit_array.sh (--array=1-N%K)

set -eo pipefail
export NCBI_DIR="$HOME/datasets/04_ncbi_wastewater_metagenome_527639"
export PIPE_DIR="$HOME/pathogen-projects/complete-chromosome-pathogen-non-pathogen-genomes"
export MAGS_INPUT="$NCBI_DIR/mags_input"
export OUT_ROOT="$NCBI_DIR/pathogen_results_perorg"   # fresh output (single-MAG run)
export MODEL_DIR="$PIPE_DIR/saved_model"
export BATCH_MB=800                  # must match submit_array.sh + merge_results.sbatch
export BATCH_MAX=400
export THREADS="${SLURM_CPUS_PER_TASK:-8}"
export ONLY_BATCH="${SLURM_ARRAY_TASK_ID}"   # <-- this task runs exactly ONE batch
CONDA_ENV="pathogen_ml"
BATCH_SCRIPT="$NCBI_DIR/batch_predict.py"

CONDA_BASE="$(conda info --base 2>/dev/null || echo "$HOME/miniconda3")"
source "$CONDA_BASE/etc/profile.d/conda.sh"
conda activate "$CONDA_ENV"

echo "host $(hostname) | array ${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID} | batch $ONLY_BATCH | cpus ${SLURM_CPUS_PER_TASK:-?} | start $(date)"
python "$BATCH_SCRIPT"
echo "end $(date)"
