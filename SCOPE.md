# Assignment scope and delivery map

**One harness, not a production clinical service.** The assignment core is probabilistic language interpretation followed by evidence validation, persistent household/member/episode state and deterministic routing. The robust memory mechanisms are part of that core, not a future add-on.

## The three deliverables

1. **Code:** this repository; entry points and model setup in [README.md](README.md). Model-backed conversation is distinct from the explicitly labeled offline parser/fixtures.
2. **Future product spec:** [PRODUCT_SPEC.md](PRODUCT_SPEC.md), a short north-star proposal rather than a list of extra assignment requirements.
3. **Scenario runs:** [SCENARIOS.md](SCENARIOS.md), exactly four locally executed, diverse transcripts. Model/search stages are declared fixtures. Broader private QA and earlier live experiments are evidence tiers, not extra submission transcripts.

## Required core

| Requirement | Mechanism and evidence | Boundary |
|---|---|---|
| Everyday health conversation | `runtime.py` intake/response prompts; bounded model calls | General questions must not manufacture symptoms or trigger unnecessary intake. |
| Implicit household/member resolution | Validated identity/profile updates and scoped state; `profiles.py`, `conversation.py` | Reliable context resolves follow-ups; ambiguity is clarified before mutation. |
| Separate and continuing episodes | `health_agent.py` episode IDs, topic/member bindings, status and observations | Another member/condition gets its own episode; closed episodes are not silently revived. |
| Persistent usable memory | SQLite, evidence-linked facts/corrections, explicit unknowns, bounded relevance retrieval; `continuity.py`, `memory_context.py`, `memory_queries.py` | Recent transcript is not the only memory. Corrections supersede prior facts; another household cannot borrow them. No vector database required. |
| Focused progressive questions | Allowed missing triage fields, known-answer state and `question_selection.py` | Ask relevant missing parameters; unknown does not become a negative answer. |
| Emergency red flags | Raw-input safety path before model/tool waits; fixed emergency response and retained holds | Contact local emergency services, never wait for email or identity registration. No autonomous call is made. |
| Noncritical doctor review | Programmatic review rules and fixed transfer response | The assignment asks for predetermined transfer wording, not actual doctor routing. The demo explicitly says no clinician is contacted. |
| Home monitoring | Deterministic required-information and risk checks | No model reassurance can bypass required escalation or missing information. |
| Extensibility | Injected model/search clients and separate validated state/policy boundaries | Lab analysis, custom knowledge base and telemedicine are not implemented core features. |

This is a bounded LLM/state/policy pipeline with a limited response→search→observation loop when the optional search adapter is used. It is not a general autonomous agent or a collection of agents. Storage and safety do not need to become model-callable tools to satisfy the assignment.

## Optional extensions requested later

- **Search/citations:** source retrieval, supporting passages and honest failures; not a custom clinical knowledge base.
- **Cache/usage/latency:** state-aware education cache and instrumentation, separate from memory correctness and provider caching.
- **Reminders/outbox:** opt-in one-shot timezone/offset/edit/cancel behavior and local delivery evidence; an awake running worker is required.
- **Human-review email/UI:** opt-in neutral synthetic notifications, known-thread polling and owner-confirmed attributed replies. A test reviewer is not a verified doctor. No bound case means no email is queued.
- **DSPy/feedback/evaluation:** reviewed synthetic examples, candidate versions, held-out/safety gates and manual promotion/rollback. An actual experiment tied; no optimized prompt is active and ordinary use does not silently train the model.
- **Browser UI:** a convenience for human testing, not an assignment-required dashboard.

These remain modular/configuration-driven; evaluating the original core does not require Gmail setup, a scheduler, DSPy, a doctor dashboard or an external telemedicine service. They must not inflate claims about what the original assignment requires.

## Size, validation and limitations

The expanded repository is larger than a minimum-lines assignment submission because it preserves later requested extensions and regression evidence. We do **not** claim it is the smallest solution. Keep one implementation and prioritize readable mechanisms with observable behavior over added frameworks, duplicate harnesses or compressed line-count tricks.

Software regressions, injected-provider contracts, actual local HTTP/SQLite, live model calls, search retrieval and real email each prove different things. See [VALIDATION.md](VALIDATION.md). Previous passes did miss the user-discovered wording/conversation sequence; its new failing-first regressions preserve the safety boundary while separating family setup and explaining retained warnings. No software suite establishes clinical safety or freedom from future bugs.
