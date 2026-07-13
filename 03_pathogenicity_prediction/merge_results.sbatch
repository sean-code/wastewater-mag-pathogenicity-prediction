#!/bin/bash
#SBATCH --job-name=pathogen_merge
#SBATCH --output=/mmfs2/home/usd.local/john.kangethe/logs/pmerge_%j.out
#SBATCH --error=/mmfs2/home/usd.local/john.kangethe/logs/pmerge_%j.err
#SBATCH --time=00:30:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --partition=compute

set -eo pipefail
export NCBI_DIR="$HOME/datasets/04_ncbi_wastewater_metagenome_527639"
export PIPE_DIR="$HOME/pathogen-projects/complete-chromosome-pathogen-non-pathogen-genomes"
export MAGS_INPUT="$NCBI_DIR/mags_input"
export OUT_ROOT="$NCBI_DIR/pathogen_results_perorg"   # must match the array job
export MODEL_DIR="$PIPE_DIR/saved_model"
export MERGE_ONLY=1                          # <-- only combines the per-batch CSVs/JSONs
CONDA_ENV="pathogen_ml"
BATCH_SCRIPT="$NCBI_DIR/batch_predict.py"

CONDA_BASE="$(conda info --base 2>/dev/null || echo "$HOME/miniconda3")"
source "$CONDA_BASE/etc/profile.d/conda.sh"
conda activate "$CONDA_ENV"

python "$BATCH_SCRIPT"
echo "merged -> $OUT_ROOT/prediction_results_ALL.csv | $(date)"
