# Bounded memory retrieval and question selection

`memory_context.retrieve` replaces wholesale model-state projection. It is deterministic, not a semantic model or a claim of smarter reasoning. It scopes access first, selects explicitly named members (otherwise the active member), preserves a compact permitted identity roster, retrieves active episodes and relevant topic-matching past episodes, then ranks permitted source-linked records by lexical relevance, uncertainty and recency. It does not build a vector store or graph.

`config.json → context` defaults to `recent_turns:6`, `max_facts:20`, `token_budget:3000`. Stored rolling history remains twelve turns; provider retrieval selects at most six. Fact units comprise member medical facts/provenance, episode observations, context assertions and optional past-episode summaries. Profile identity metadata and unknown-field annotations are retained separately. These are configurable upper bounds, not a guarantee that twenty facts or six full turns always fit.

`token_counter:cached_tiktoken` uses the verified local o200k vocabulary for the explicitly supported GPT-5-mini/GPT-4.1-mini aliases. It counts the same JSON spelling used in dynamic memory, with a manifest margin. No tokenizer download occurs inside a chat request. The current isolated DSPy environment includes tiktoken; its public vocabulary is prepared under `/tmp/family-health-tokenizer-cache`. Other environments can install `requirements-runtime.txt` and explicitly prepare that temporary cache using `TIKTOKEN_CACHE_DIR=/tmp/family-health-tokenizer-cache python -c "import tiktoken; tiktoken.encoding_for_model('gpt-5-mini')"`.

Unavailable dependencies/cache, unsupported models, or explicit `token_counter:utf8` retain the conservative UTF-8 byte upper bound. That fallback may underutilize the window or report overflow; it is not native token counting. The bound covers **serialized dynamic memory**, including its manifest—not fixed prompts/schema, current message or later tool observations. It does not claim that the entire API request fits 3,000 tokens. Provider receipts remain the source for full-request usage. Safety-critical context is never silently dropped.

Selected medical facts (allergies/conditions/medications), active episode observations and active structured clinical context are protected. Current false values remain explicit negatives; absent fields are marked unknown rather than invented negative findings. Evidence, timestamps, uncertainty and immutable member/episode IDs are preserved. Current profile correction evidence remains available. Superseded/retracted records, deleted-source tombstones and old derived summaries cannot override current facts.

Consolidation is a bounded, on-demand source projection: duplicate identical assertions are collapsed, current source records remain linked, and two conflicting active values are both retained instead of resolved by frequency/recency. No LLM writes a new medical summary, inferred diagnosis or certainty. The projection is rebuilt from current state after correction/deletion; no persistent lossy summary cache can resurrect deleted content. Source hashes/accounting are diagnostic and excluded from reusable answer-cache keys; actual selected facts remain in those keys.

Optional history, raw reports and past/source excerpts are omitted before critical facts. If selected critical facts or their accounting cannot fit, `ContextOverflow` returns an explicit `context_overflow` response and the affected provider stage is skipped. No critical record is silently discarded to meet a cap. If a model already supplied validated updates before response projection overflows, those updates are saved and the trace says so. Large cases may need a narrower member/episode request or a reviewed larger budget. Emergency lexical guidance still runs before any memory retrieval/provider dependency.

Access-restricted views omit other members; global preference evidence that could contain unrelated health text is replaced with a source-linked generic provenance label. Retrieval does not grant permissions or reveal a hidden member's medical facts through the identity roster.

## Adaptive next-question selection

`question_selection.choose` selects only from the policy's **allowed missing fields**. Red flags are always asked first when missing. Existing fixed order remains the default for compatibility. An optional test config can enable:

```
"question_selection": {"mode":"adaptive"}
```

Adaptive mode prioritizes hydration for stomach symptoms, duration for fever, and severity for headache/cough, after red flags. `topic_priorities` can select a different order from existing question keys; invented/answered fields are ignored. Existing yes/no binding follows the actual selected field. No clinical thresholds, escalation text, medical diagnosis rules or outbound actions are changed. This is deterministic contextual prioritization, not learned information gain.

These are anticipatory questions **during an active conversation**, not unsolicited outreach. No proactive health check-in, email or notification is sent. Such outreach and clinical policy changes still require separate design/authorization.

Tests include known-positive/negative/unknown facts, corrected laterality, uncertain causes, duplicate/conflicting records, stale summaries/tombstones, scope, thousand-turn history, fact/token overflow, validated-update persistence, explicit member switching, past-topic relevance, newer irrelevant chatter and adaptive answer binding. Passing these synthetic retrieval oracles is not live-model recall quality or clinical validation.
