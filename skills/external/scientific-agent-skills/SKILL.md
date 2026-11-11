---
name: scientific-agent-skills
description: Router for validated scientific research workflows across biology, chemistry, medicine, data analysis, statistics, and computational science
triggers: [scientific research, scientific agent, biology, chemistry, genomics, bioinformatics, drug discovery, clinical research, statistical analysis, research paper]
priority: 85
---
# Scientific Agent Skills router

Use the most specific installed skill under `skills/external/scientific-*` for scientific workflows. Read its `SKILL.md` before applying it; follow its references and scripts only after reviewing what they do. Do not load the entire scientific catalogue into every prompt.

Separate hypotheses, measured data, published findings, and model-generated suggestions. Prefer primary literature, official databases, and reproducible analyses. Include citations, data provenance, units, software versions, assumptions, and limitations. Never fabricate experimental results or claim clinical validity from a skill instruction alone.

Some scientific skills require optional packages, API keys, datasets, or external services. Check prerequisites before execution and do not install large or privileged dependencies unless needed for the requested task. Clinical or patient-specific decisions require appropriate professional oversight.

Run `scripts/install-system-skills.sh` to fetch the upstream collection and populate individual on-demand skill packages. The installed commit is recorded in `skills/external-sources.lock`.

Upstream: https://github.com/K-Dense-AI/scientific-agent-skills
