# Engaging Cluster Fine-tuning Walkthrough

The Engaging cluster is MIT's HPC cluster. You get GPU access through your lab/department.

## 1. Get access

If you don't already have an account:
- Request at: engaging-support@techsquare.com
- Include your MIT ID, PI name, and partition you need access to
- Usually approved within 1-2 business days

## 2. Connect

```bash
ssh <your-kerberos>@eofe7.mit.edu   # or eofe8, check which node your group uses
```

## 3. Transfer the project

From your laptop:
```bash
# From the project root — exclude venv and data (too large)
rsync -av --exclude='.venv' --exclude='data/edgar' --exclude='__pycache__' \
  "c:/Users/david/MyDrive/coding projects/docinfo/" \
  <kerberos>@eofe7.mit.edu:~/docinfo/
```

## 4. Check available GPU partitions

```bash
sinfo -s   # list all partitions
# Common MIT partitions with GPUs:
#   sched_mit_sloan_gpu
#   sched_mit_hill (general)
#   sched_engaging_default
# Ask your lab which one you have access to
```

## 5. Update the partition in the job script

Edit `finetune_engaging.sh` and change:
```bash
#SBATCH --partition=sched_mit_sloan_gpu   # → your actual partition
```

## 6. Submit the job

```bash
cd ~/docinfo
mkdir -p logs
sbatch finetune_engaging.sh

# Check status
squeue -u <your-kerberos>

# Watch the output live
tail -f logs/finetune_<job_id>.out
```

## 7. Expected runtime

With 1 GPU (A100 or V100), 10 epochs, 300 pairs/class, 512 chars:
- Fine-tuning: ~20-40 minutes
- Retraining classifiers: ~5 minutes
- Total: under 1 hour

## 8. Copy results back to your laptop

```bash
# After job completes:
rsync -av <kerberos>@eofe7.mit.edu:~/docinfo/model/ \
  "c:/Users/david/MyDrive/coding projects/docinfo/model/"
```

Then restart your local server — it will automatically use the new model files.

## Expected accuracy improvement

With 10 epochs on GPU vs 1 epoch on CPU:
- Industry classifier: ~90% → 95%+ with strong confidence scores (0.6-0.8 rescaled)
- Doc type classifier: already at 96%, minimal change expected

The key win is confidence, not just accuracy — the fine-tuned embeddings will
separate adjacent industries (healthcare/pharma, finance/PE) cleanly in embedding
space, so predictions will be decisive rather than spread across nearby classes.
