# Capability audit — Kwee Field Journal

This matrix maps every item from the supplied [`PLANNING-REQUEST.txt`](PLANNING-REQUEST.txt)
to the behavior that actually exists in this repository. A capability is not marked complete
just because its name appears in a prompt or a menu.

## Status legend

- **NATIVE** — deterministic code stores, validates, indexes or executes the
  stated operation. This does not make the stored claim true.
- **PARTIAL** — a bounded heuristic or a limited workflow exists; the broader
  capability in the planning list is not fully implemented.
- **MODEL-GUIDED** — Kwee can direct the configured language model to attempt
  the behavior using explicit prompts and caveats. There is no independent
  algorithmic guarantee that the model performed it correctly.
- **NOT IMPLEMENTED** — no real implementation or testable behavior is present.

The entries describe software behavior, not a claim that the underlying model
has acquired human-like cognition, learned new model weights, or achieved
scientific validity.

## Full mapping

| # | Capability from planning | Status | Implemented behavior and boundary |
|---:|---|---|---|
| 1 | Abstract Pattern Recognition | MODEL-GUIDED | `agent/cognitive.py` detects an abstract-pattern intent and adds model guidance. No standalone pattern-recognition engine is provided. |
| 2 | Universal Structure Discovery | MODEL-GUIDED | The model may compare structures across domains. No universal structure miner or cross-domain validation algorithm exists. |
| 3 | Knowledge Reconciliation | NATIVE / PARTIAL | `memory/knowledge.py` detects different objects for the same exact subject/predicate when their validity intervals overlap. It flags the conflict; it does not decide which claim is true. |
| 4 | Knowledge Decomposition | MODEL-GUIDED / NATIVE LINKS | The model can propose decomposition; the store can save explicit `decomposed_into` links. Decomposition is not automatically discovered or verified. |
| 5 | Knowledge Reconstruction | NATIVE | A bounded traversal reconstructs only explicit `supported_by`, `depends_on` and `derived_from` links, retaining claim IDs and provenance. It does not fill missing facts. |
| 6 | Knowledge Generalization | MODEL-GUIDED | The model can suggest a generalization. No inductive rule learner or out-of-domain validator exists. |
| 7 | Knowledge Specialization | MODEL-GUIDED | The model can suggest domain-specific instances or refinements. No automatic ontology-specialization engine exists. |
| 8 | Knowledge Dependency Modeling | NATIVE / PARTIAL | Explicit typed claim-to-claim relations and bounded traversal are stored. Dependencies must be supplied; they are not inferred as ground truth. |
| 9 | Knowledge Revision | NATIVE | A revision creates a new claim, preserves the prior version, records a supersession relation and closes the old validity interval where appropriate. The caller initiates revisions. |
| 10 | Non-monotonic Reasoning | PARTIAL | Conflict states and superseded claims are retained, and prompts warn against silently keeping old conclusions. No general-purpose defeasible logic engine exists. |
| 11 | Temporal Reasoning | PARTIAL | Claims have `valid_from`/`valid_until` timestamps; searches can be time-scoped and observations are timestamped. Temporal interpretation beyond those operations is model-guided. |
| 12 | Spatial Abstraction Reasoning | NOT IMPLEMENTED | A prompt mode can identify spatial language, but the repository has no coordinate system, geometry engine, spatial index or validated spatial abstraction solver. |
| 13 | Causal Inference | NOT IMPLEMENTED | Prompts request estimands, confounders and caution about association. There is no causal identification estimator, DAG adjustment engine or intervention model. |
| 14 | Counterfactual Reasoning | MODEL-GUIDED | The model can reason through stated counterfactual assumptions. No structural causal model or counterfactual simulator exists. |
| 15 | Analogical Transfer | MODEL-GUIDED | The model can map source/target concepts; the store supports an explicit `analogous_to` edge. An analogy is not evidence or proof. |
| 16 | Multi-perspective Reasoning | MODEL-GUIDED | Intent routing asks the model to consider multiple viewpoints. There is no independently measured completeness or viewpoint-diversity guarantee. |
| 17 | Adversarial Reasoning | MODEL-GUIDED | Guidance asks for counterarguments, rivals and failure cases. No formal adversarial search or proof checker exists. |
| 18 | Continual Knowledge Acquisition | NATIVE / PARTIAL | Tenant-local SQLite stores claims and evidence across turns. Acquisition is explicit through tools and agent actions; the system does not autonomously ingest the whole internet. |
| 19 | Incremental Learning | PARTIAL | Explicit user feedback updates bounded tenant-local tool/response preference statistics. It changes ordering/guidance, not model weights or domain knowledge by itself. |
| 20 | Few-example Concept Learning | NOT IMPLEMENTED | No evaluated few-shot concept learner, support-set algorithm or concept benchmark is implemented. The underlying LLM may respond to examples, but that is not a Kwee subsystem. |
| 21 | One-shot Concept Learning | NOT IMPLEMENTED | No one-shot learner or measured one-example generalization subsystem exists. |
| 22 | Meta-learning | NOT IMPLEMENTED | The preference ranker is a bounded online heuristic, not a learned optimizer that learns how to learn across tasks. |
| 23 | Learning-to-Learn | PARTIAL | Feedback and bounded strategy guidance can affect future tool/response ordering. No independently evaluated reusable learning strategy is trained. |
| 24 | Self-supervised Representation Learning | NOT IMPLEMENTED | No self-supervised encoder, embedding training pipeline, training corpus or weight update path exists. |
| 25 | Active Information Acquisition | MODEL-GUIDED | The model can be prompted to identify what evidence would reduce uncertainty. No active-learning acquisition function or utility optimizer is implemented. |
| 26 | Capability Self-assessment | PARTIAL | The system exposes capability labels and test-backed feature status. This is a repository-level inventory, not an empirical calibration of an LLM's general competence. |
| 27 | Knowledge Boundary Awareness | PARTIAL | Capability guidance and explicit unknowns are available. There is no general epistemic-state monitor capable of proving that the model knows what it does not know. |
| 28 | Cognitive Process Monitoring | NOT IMPLEMENTED | Tool events and audit records can be inspected, but private model reasoning is not observable or objectively monitored. No hidden-chain-of-thought capture is added. |
| 29 | Strategy Introspection | PARTIAL | The system records tool outcomes/latency and accepts explicit feedback for bounded routing changes. It does not reliably introspect internal model strategy. |
| 30 | Confidence Calibration | NOT IMPLEMENTED | Confidence can be recorded as caller-supplied metadata. No calibration dataset, Brier score, reliability curve or calibrated probability output is generated. |
| 31 | Cognitive Error Attribution | PARTIAL | Structured tool errors and audit outcomes exist. No automatic cognitive-root-cause attribution model is implemented. |
| 32 | Reasoning Strategy Adaptation | PARTIAL | Regex-based intent routing selects bounded prompts and feedback can affect tool order. This is heuristic adaptation, not an optimizer for reasoning quality. |
| 33 | Cognitive Failure Prediction | NOT IMPLEMENTED | No validated predictor estimates the probability of a task or reasoning failure before execution. |
| 34 | Self-generated Problem Solving | MODEL-GUIDED | The model can propose exercises or subproblems when prompted. No dedicated generator, difficulty controller or evaluation loop exists. |
| 35 | Research Direction Optimization | MODEL-GUIDED | Research plans can be compared in natural language. No formal utility, cost or evidence-gain optimizer chooses a globally optimal direction. |
| 36 | Discovery Validation | PARTIAL | Exact-key conflict checks, provenance links and recorded observations support review. The system does not independently verify the truth or novelty of a discovery. |
| 37 | Intellectual Development Tracking | PARTIAL | Tenant-local feedback and tool/learning statistics provide operational history. No validated longitudinal measure of intellectual ability exists. |
| 38 | Open-ended Knowledge Discovery | MODEL-GUIDED | Existing research/search tools can be composed for exploration. No unattended autonomous discovery engine or novelty guarantee exists. |
| 39 | Associative Retrieval | PARTIAL | `memory/intelligence.py` uses normalized token overlap plus recency and explicit links; `memory/knowledge.py` stores typed relations. There is no embedding-based vector search or automatic semantic graph traversal. |
| 40 | Temporal Knowledge Indexing | NATIVE | SQLite indexes validity timestamps and observations carry event times. Temporal queries are bounded by the stored metadata and query implementation. |
| 41 | Hierarchical Knowledge Compression | PARTIAL | Existing memory compression merges similar entries and stores source IDs/provenance in metadata. It is heuristic, may lose nuance and is not guaranteed lossless. |
| 42 | Memory Consolidation Engine | PARTIAL | Local memory summaries/consolidation helpers exist. There is no learned cognitive consolidation engine or automatic proof that summaries preserve all relationships. |
| 43 | Memory Interference Resolution | PARTIAL | Exact subject/predicate claims with overlapping time intervals are marked disputed. Similarity-based, cross-entity and general semantic conflicts are not comprehensively detected. |
| 44 | Knowledge Provenance Tracking | NATIVE | Claims can store source URI, label, evidence, epistemic type, validity and supersession/derivation links. Correctness still depends on the caller supplying accurate source data. |
| 45 | Semantic Memory Reorganization | PARTIAL | Explicit typed edges and revisions can reorganize knowledge records. No automatic semantic graph restructuring or embedding refresh is performed. |
| 46 | Memory Reliability Estimation | NOT IMPLEMENTED | The stored confidence is supplied by the caller and is not an independently estimated reliability score. Source presence alone is not source quality. |
| 47 | Scientific Question Formation | MODEL-GUIDED / NATIVE STORAGE | The model can draft a research question; `research_create` persists a caller-supplied title/question/scope. The store does not score question quality automatically. |
| 48 | Literature-based Hypothesis Formation | MODEL-GUIDED / NATIVE STORAGE | Hypotheses, alternatives, predictions and falsification criteria can be persisted. Completeness depends on the actual literature search and source evidence; no literature review is implied. |
| 49 | Research Gap Identification | MODEL-GUIDED | The model can identify apparent gaps within evidence it has seen. No exhaustive database coverage or novelty guarantee exists. |
| 50 | Experimental Variable Modeling | NATIVE WORKFLOW | Study records accept independent/dependent variables, confounders, controls, sampling, analysis and stopping rules. Values are supplied by the caller; their scientific suitability is not certified. |
| 51 | Experimental Methodology Construction | PARTIAL WORKFLOW | The workbench stores a draft study design and checks basic required fields. It does not run ethics, safety, feasibility or regulatory approval. |
| 52 | Statistical Hypothesis Testing | PARTIAL | A two-sided independent-samples Welch t-test is implemented, with effect estimate, interval and Bonferroni adjustment for a declared comparison count. It is not a general statistical package. |
| 53 | Experimental Result Analysis | PARTIAL | The same bounded Welch workflow can analyze caller-provided numeric groups. It does not validate measurement quality, sampling, independence or causal assumptions. |
| 54 | Replication Study Generation | MODEL-GUIDED / NATIVE STORAGE | A model can draft a replication plan and the workbench can persist a study design. No replication experiment or data collection is run automatically. |
| 55 | Scientific Theory Evaluation | MODEL-GUIDED / PARTIAL EVIDENCE | The model can appraise arguments using stored provenance and counterclaims. No formal theorem prover or automated scientific theory validator exists. |

## Native workbench entry points

The owner-only `knowledge_engine` tool exposes these implemented actions:

- `status` — inspect the declared capability boundaries.
- `claim_add`, `claim_search`, `claim_reconcile`, `claim_revise`,
  `claim_link`, `claim_provenance`, `claim_reconstruct` — store and inspect
  evidence-bounded claims.
- `research_create`, `hypothesis_add`, `study_design`, `observation_add`,
  `research_report` — persist a research workflow with rival hypotheses and
  recorded observations.
- `welch_test` — run the narrow independent-samples Welch analysis described
  above. It requires the caller to attest that the design assumptions were
  considered; the program cannot verify those assumptions from two arrays.

The tool stores data below the active tenant's storage root. It rejects
caller-supplied database paths. Conflict detection preserves disagreement and
history rather than silently declaring the newest record to be true.

## Performance and native-code policy

The upstream RTK executable is already a compiled Rust CLI used to rewrite
supported shell commands. It is not a Rust extension of Kwee's Python/Node
runtime. No Assembly code or new Rust FFI module was added: this archive's
runtime does not have a demonstrated CPU hotspot or a configured Rust toolchain
with which to benchmark and validate such a change. Adding low-level code
without a measured bottleneck would increase maintenance and ABI risk without
proof of faster task execution. Profile first; introduce Rust only for a
measured hot path with a stable interface, and Assembly only if profiling
proves the Rust/compiler output is still a material bottleneck.

## Verification boundaries

The repository test suite exercises the database, tool registry, agent-loop
routing, security revalidation and statistical helper using deterministic test
fixtures. A test double is not a test against a live provider or live RTK binary.
WhatsApp login/message delivery requires valid credentials and a live session;
X/Twitter requires a configured authenticated Agent Reach backend; YouTube
retrieval requires the runtime's `yt-dlp` backend. Those external integrations
must be smoke-tested in the operator's configured environment.
