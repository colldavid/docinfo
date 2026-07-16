#!/bin/bash
#SBATCH --job-name=docinfo-finetune
#SBATCH --output=logs/finetune_%j.out
#SBATCH --error=logs/finetune_%j.err
#SBATCH --time=02:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --gres=gpu:1
#SBATCH --partition=sched_mit_sloan_gpu   # adjust to your lab's partition

# ── Setup ─────────────────────────────────────────────────────────────────────
set -e
echo "Job started: $(date)"
echo "Node: $(hostname)"
nvidia-smi

# Load modules (Engaging uses Lmod)
module load python/3.12.0
module load cuda/12.1

# Create venv if not already present
if [ ! -d ".venv" ]; then
    python -m venv .venv
fi
source .venv/bin/activate

pip install --quiet -r requirements.txt
pip install --quiet datasets accelerate sentence-transformers

mkdir -p logs model/embedding

# ── Run fine-tuning ────────────────────────────────────────────────────────────
PYTHONPATH=. python finetune.py \
    --mode both \
    --epochs 10 \
    --batch-size 64 \
    --pairs-per-class 300 \
    --max-chars 512 \
    --output-dir model/embedding/finetuned

echo "Fine-tuning done: $(date)"

# ── Retrain classifiers on fine-tuned embeddings ──────────────────────────────
PYTHONPATH=. python train.py --embedding-model model/embedding/finetuned

echo "Retraining done: $(date)"
echo "Accuracy results above. Copy model/ back to your laptop with scp."
