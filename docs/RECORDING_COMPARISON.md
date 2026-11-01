# What retained evidence added in one reader exercise

Two fresh Claude Code sessions answered the same 100 questions about 20 small synthetic projects. One received initial and final source; the other received that same material plus actual persisted recording exports. Both correctly described current source and preserved the main uncertainty about authors, exact save times, unseen activity and failure causes. The evidence session recovered useful historical details, and also made five over-strong or contradictory history claims.

The [sanitized question-level receipt](recording-reader-results.json) contains all 200 questions and answers, reviewed outcomes, rationales, case-local citation IDs and input/output hashes. It excludes raw prompts, local status packets, machine paths and private data. Frozen original outputs were preserved; grading made no additional model calls. An evaluator coding agent adjudicated the answers case by case, and the coordinating coding agent reviewed the decisions. This was agent review within the same development workflow, not an independent human or third-party study.

## Correctness and answerability are different

| Question group | Source only | With persisted evidence |
| --- | --- | --- |
| Current source: 20 questions | 20 grounded correct | 20 grounded correct |
| History: 40 questions | 40 grounded correct, with appropriate uncertainty about unavailable history | 32 grounded correct, 3 legitimate partial, 5 containing unsupported or contradictory clauses |
| Honesty: 40 questions | 40 grounded correct | 40 grounded correct |

**Correctly saying “the supplied source cannot establish that history” counts as correct.** The source-only result is not a failure. If grounded correctness and legitimate partial answers are combined, the broad counts are **100/100 for source only and 95/100 with evidence**. That measures grounding and calibration, not recovery of hidden events or completion of every requested historical detail.

The separate utility result is that the evidence session supplied additional verified history in **25 question answers: 22 fully grounded and three qualified partial**, compared with zero additional history answers from boundary source alone. These are answer counts, not distinct events. Ten other evidence-history answers accurately described empty supplied records and boundary source. Five contained reviewable over-claims; their useful portions do not erase those clauses.

Examples of useful retained details were a temporary value followed by restoration, deletion and recreation, an intermediate omitted config field, tool-mediated versus unknown-author changes, a value preserved across different observation sessions, and an intermediate exact hash. Identical final files alone could not establish those histories.

All 102 cited ID occurrences were present in the relevant case. That is an ID-integrity check, not proof that every sentence follows from its citation. Independent review applied the same conservative rule to combined-state assertions: overlapping per-file intervals do not prove a captured simultaneous global state, and a non-atomic disclaimer does not retract an affirmative coexistence claim. Another answer confused newest-first result display with journal insertion order.

Legitimate partial answers were retained rather than marked wrong because the producer knew more. The public API did not establish one intermediate newline profile or a complete first-restart chronology. Correct retained values received credit; unavailable details remained unknown. Neither condition invented the deliberately unobservable stopped-recorder save/revert control.

## Diagnostic first, guardrails afterward

This receipt evaluates the original frozen 0.3.0 implementation, including engine SHA-256 `0bdb2aa5f2618e9819589aee0e6ca26a129c8f4422507b1783d89f800c190302` and recorder SHA-256 `ad0bcd1d848e1965221d7dcb30b206ebac98c75ee70f3d46e5751534849c49a6`. The receipt lists the other evaluated core hashes.

After seeing the diagnostic, runtime metadata and MCP instructions were refined to explicitly label conservative scan bounds, non-atomic per-file events and journal order versus save order. Capture, storage and merge behavior were unchanged. **The effect of those later wording guardrails on model answers is unmeasured.** The reader batches were not rerun to produce a better score, and their original prompts, outputs and grading provenance remain intact.

The [portable recording regression](../benchmarks/recording/README.md) separately checks 20 synthetic cases and 29 expected stable file transitions: 27 unknown-author observations plus two self-reported tool-recorded changes. It preserves nine distinguishable histories and one intentionally indistinguishable control. Those mechanical counts are not the accuracy of the 200 reader answers above. The original pre-inspection corpus is now a public development fixture set, not a fresh unseen external benchmark.

## Limits

This was one run per condition using two fresh **batch** sessions, not a fresh context per individual case. Each session could see both members of a pair without pairing labels. Cross-case inference was forbidden, but complete per-case isolation was not independently guaranteed. Coding agents performed the semantic adjudication and review; exact decisions are preserved for inspection.

Source and questions were identical, but adding evidence increased input size from **23,005 to 90,229 UTF-8 bytes**. Outputs were 24,428 and 27,400 bytes. This is not equal-context-length testing or a measurement of reasoning superiority. Both bridge stdout receipts reported the sole `modelUsage` key **`claude-opus-5-5`**. Exact reasoning and sampling settings are unavailable and are not inferred from defaults.

The sample is small, synthetic and deliberately stable at checkpoints. It is not real-user validation, a production failure study, a polling capture-rate test, a population accuracy estimate or proof of security. Source differences do not prove every save, authenticated authorship, exact action timing, semantic rename commands, passed checks or failure causality.

The practical result is narrower: retained evidence made specific historical details available after context loss. A reader still needs to keep its conclusions within the evidence. There is no blanket model winner or general Claude/Codex superiority claim.
