---
name: ai-research
description: "Router untuk 34 sub-skill penelitian AI (autoresearch, RAG, fine-tuning, multimodal, agents, paper writing, inference serving, dsb). Pakai tool skill action=get name=... file=... untuk mengakses sub-skill spesifik."
triggers: [research, penelitian, ai research, autoresearch, ml paper, fine-tuning, rag, multimodal, agents, inference, mlops, evaluation, optimization, mechanistic interpretability, data processing, distributed training, safety alignment, prompt engineering, observability, emerging techniques, research ideation, agent native]
priority: 30
---
# AI Research — Router Skill

Skill ini adalah **router** ke 34 sub-skill penelitian AI di folder `skills/external/ai-research/`. Loader Kwee hanya mengenali skill yang punya `SKILL.md` di level folder-nya (level-1). Karena repo asli tidak punya SKILL.md di root, file ini dibuat sebagai wrapper.

## Sub-skill yang tersedia

| Sub-skill | Folder | Deskripsi singkat |
|-----------|--------|-------------------|
| autoresearch | `0-autoresearch-skill` | Orkestrasi penelitian otonom (two-loop architecture) |
| model-architecture | `01-model-architecture` | Arsitektur model (Transformer, MoE, SSM, dll) |
| tokenization | `02-tokenization` | Tokenizer (BPE, SentencePiece, tiktoken) |
| fine-tuning | `03-fine-tuning` | LoRA, QLoRA, full fine-tune, DPO, RLHF |
| mechanistic-interpretability | `04-mechanistic-interpretability` | SAE, probing, activation patching, circuits |
| data-processing | `05-data-processing` | Cleaning, dedup, tokenization pipeline, quality filtering |
| post-training | `06-post-training` | SFT, RLHF, RLAIF, preference optimization |
| safety-alignment | `07-safety-alignment` | Red-teaming, constitutional AI, refusal steering |
| distributed-training | `08-distributed-training` | FSDP, DeepSpeed, ZeRO, pipeline parallelism |
| infrastructure | `09-infrastructure` | Cluster, scheduling, GPU networking, storage |
| optimization | `10-optimization` | Optimizer, LR schedule, mixed precision, compilation |
| evaluation | `11-evaluation` | Benchmarks, LLM-as-judge, human eval, robustness |
| inference-serving | `12-inference-serving` | vLLM, TGI, TensorRT-LLM, batching, speculative decoding |
| mlops | `13-mlops` | Experiment tracking, CI/CD, model registry, monitoring |
| agents | `14-agents` | Tool use, planning, memory, multi-agent, eval |
| rag | `15-rag` | Retrieval, embedding, reranking, long-context, graph RAG |
| prompt-engineering | `16-prompt-engineering` | Few-shot, CoT, self-consistency, DSPy, optimization |
| observability | `17-observability` | Logging, tracing, metrics, alerting, debugging |
| multimodal | `18-multimodal/*` | Vision, audio, video, robotics (9 sub-sub-skill) |
| emerging-techniques | `19-emerging-techniques` | Speculative decoding, Mamba, RWKV, quantization |
| ml-paper-writing | `20-ml-paper-writing/*` | Paper writing, plotting, conference talks (3 sub) |
| research-ideation | `21-research-ideation` | Brainstorming ide penelitian, gap analysis |
| agent-native-research-artifact | `22-agent-native-research-artifact/*` | Rigor reviewer, compiler, research manager (3 sub) |

## Cara pakai

```python
# Via tool skill — muat sub-skill tertentu
skill(action="get", name="0-autoresearch-skill", file="SKILL.md")
skill(action="get", name="15-rag", file="SKILL.md")
skill(action="get", name="20-ml-paper-writing/ml-paper-writing", file="SKILL.md")

# Atau baca rujukan di dalam sub-skill
skill(action="get", name="0-autoresearch-skill", file="references/agent-continuity.md")
```

## Catatan

- Setiap sub-skill punya `SKILL.md` sendiri + folder `references/` berisi dokumen teknis detail
- Gunakan `skill(action="list")` untuk lihat daftar skill level-1 (ini termasuk)
- Sub-skill **tidak** muncul di katalog utama — panggil eksplisit via tool `skill`