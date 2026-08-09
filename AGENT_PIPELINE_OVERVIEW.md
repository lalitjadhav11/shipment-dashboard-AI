# How the AI Shipment Assistant Answers a Question

A plain-language walkthrough of what happens between a user typing a question and the agent
replying — written for a team demo, not as a spec. For the full technical detail behind every
decision here, see `AGENTIC_RAG_ARCHITECTURE.md`.

## The big idea

Most questions are answered **without calling any LLM at all** — a fast, free, deterministic
pipeline (`v0`) handles anything it recognizes using 28 pre-built, pre-tested SQL templates. Only
when nothing fits does the system fall back to an LLM (`v1`) to draft a brand-new query and write
the answer in prose. This keeps answers instant and free for the common cases, and reserves the
slower/costlier LLM path for genuinely novel questions.

```
User question
     │
     ▼
┌─────────────────────┐
│ 1. Intent Classifier │  "which of our 28 known questions is this closest to?"
└─────────┬───────────┘
          │ (runs in parallel with Stage 2)
┌─────────▼───────────┐
│ 2. Entity Extractor  │  "what tracking IDs / names / dates / statuses are mentioned?"
└─────────┬───────────┘
┌─────────▼───────────┐
│ 3. Schema Scoper     │  "which 4-ish tables actually matter for this question?"
└─────────┬───────────┘
          │
     ┌────┴────┐
     │ template │──yes──▶ 4a. Fill the matching template (free, instant)
     │  fits?   │
     └────┬────┘
          │no
┌─────────▼───────────┐
│ 4b. LLM drafts SQL   │  (only reached when nothing else fits)
└─────────┬───────────┘
┌─────────▼───────────┐
│ 5. Guardrail Check   │  "is this query actually safe to run?"
└─────────┬───────────┘
┌─────────▼───────────┐
│ 6. Execute (read-only)│
└─────────┬───────────┘
     ┌────┴────┐
     │ from a  │──template──▶ 7a. Fill the matching answer sentence (free, instant)
     │template?│
     └────┬────┘
          │LLM
┌─────────▼───────────┐
│ 7b. LLM writes the   │
│    final answer      │
└──────────────────────┘
```

---

## Stage 1 — Intent Classifier *(no LLM)*

**What it does:** turns the question into a vector ("embedding") and compares it against 28
pre-written example questions — one per supported intent. Picks whichever is closest.

**Special cases:**
- **Confidence floor.** If nothing scores above 0.40 similarity, the classifier honestly says "I
  don't know" instead of guessing — tuned against real out-of-domain questions ("what's the
  weather") vs. real paraphrases of supported questions, so it's neither too trigger-happy nor
  too stubborn.
- **No forced choice.** Unlike some classifiers, it's allowed to return "no match" — that's what
  lets Stage 4b's LLM fallback exist at all.

## Stage 2 — Entity Extractor *(no LLM)*

**What it does:** pulls concrete values out of the raw text — tracking IDs, status/package/
delivery-type words, customer names, city names, dates — using regex and fuzzy string matching,
not an LLM.

**Special cases:**
- **Typo/spacing tolerant tracking IDs.** `"800000000019give me..."` (no space) still correctly
  extracts the tracking ID — the matcher looks for "9-15 digits not glued to another digit,"
  not "9-15 digits with clean word boundaries," so it survives missing spaces and stray characters.
- **Fuzzy status matching without false positives.** "customs" correctly matches the status
  `CUSTOMS_HOLD` even though it's not an exact match — but "pallet package shipments" does
  **not** incorrectly match `LOST_PACKAGE` just because the word "package" appears in both.
- **Multiple tracking IDs.** If someone mentions two, both are captured — so the pipeline can
  notice and ask "which one?" instead of silently answering about only the first.
- **Relative dates.** "more than a week" is resolved against *today's actual date*, not just
  pattern-matched as text.

## Stage 3 — Schema Scoper *(no LLM)*

**What it does:** ranks every table/view in the database schema by relevance to the question and
keeps only the top few — this narrow slice is the *only* thing ever shown to an LLM later, which
is what keeps hallucination risk low and prompts small.

**Special cases:** three situations where the *right* table wouldn't naturally rank high enough
by keyword similarity alone, so it gets force-included instead:
- A **specific thing is being asked about** (a tracking ID, a customer name, a city, or an
  explicit "give me 5 shipments") → the raw shipments table is forced in, even if a summary view
  scored higher.
- A **"why" question** → the table with real incident descriptions is forced in, even though its
  own field names don't share vocabulary with how people phrase "why" questions.
- A **"history/timeline" question** → the journey-log table is forced in, for the same reason.

## Stage 4a — Template SQL *(no LLM — the fast path)*

**What it does:** 28 pre-written, pre-tested SQL queries covering the most common shapes of
question (single-shipment lookups, fleet-wide dashboards, "shipments filtered by X"). If one
matches, it runs instantly for zero cost.

**Special cases:**
- **Bare tracking ID** ("700000000001" alone) → defaults to "where is my package," a safe guess.
- **A tracking ID plus a more specific question** ("what was the *previous* stage of X") →
  does **not** force the generic "where is it" template just because an ID is present — that
  would answer confidently but wrong. Falls through to the LLM instead.
- **A fleet-wide-sounding match despite a tracking ID being present** → corrected to the
  shipment-specific template rather than answering an irrelevant company-wide report.
- **Multiple tracking IDs** → declines with "which one did you mean?" instead of guessing.
- **A "why" question that matched a template that can't explain causes** (like a plain count) →
  rejected on purpose, sent to the LLM instead, so the answer actually addresses "why."
- **A "show me all X" question that matched a summary/aggregate template** instead of an actual
  list → same rejection, same reason.

## Stage 4b — LLM Drafts SQL *(fallback only — a real LLM call)*

**What it does:** only reached when nothing in Stage 4a fits. The LLM writes a brand-new SQL
query — but only using the narrow table list from Stage 3, and only using values Stage 2 already
extracted (it can never invent a tracking ID, name, or date on its own).

**Special cases:**
- **Explicitly told which table matters and why** for "why" and "history" questions — not left to
  infer it from a generic table list, because it doesn't always reach for the right one on its own
  even when it's available.
- **Told exactly what "today" is**, so relative-date reasoning ("more than a week ago") works.
- **Told that an extracted date is already fully resolved** — so it doesn't accidentally subtract
  a week *twice* by treating an already-resolved cutoff as if it were "today."

## Stage 5 — Guardrail Validator *(no LLM — the safety gate)*

**What it does:** every generated query — whether from a template or the LLM — is parsed and
checked *before it ever touches the database*: SELECT-only, only from the approved table list
Stage 3 produced, only real or clearly-computed columns.

**Special cases:**
- Correctly recognizes computed aliases (like a running count) as legitimate, even nested inside
  sub-queries — without that check, some perfectly safe queries would be wrongly rejected.

## Stage 6 — Execute *(read-only)*

**What it does:** runs the approved query through a database account that can only ever read,
never write, with a timeout so nothing runs forever.

**Special cases:**
- A query that passed every safety check can still fail at runtime (a timeout, a database hiccup)
  — caught cleanly and turned into an honest "couldn't get an answer" instead of crashing the
  response.

## Stage 7a — Template Response *(no LLM — the fast path)*

**What it does:** turns the raw rows from a template query into a clean, pre-written sentence.

**Special cases:**
- **Every list-style answer now shows the true total**, not just what's displayed — "20 of 600
  shipments," not a bare "20" that quietly implies that's everything.
- **"Any open issues?" also surfaces resolved/closed ones** when there's nothing currently open —
  so a shipment that *had* a problem, now fixed, doesn't get an incorrect "no issues at all."

## Stage 7b — LLM Synthesizes the Answer *(fallback only — a real LLM call)*

**What it does:** only used for Stage 4b's LLM-drafted queries — turns raw rows into a natural
prose answer.

**Special cases:**
- **Told exactly what SQL already ran and what it filtered on** — otherwise it can wrongly claim
  "that information isn't available" just because a filter column isn't repeated in the output,
  when the filtering already happened correctly server-side.
- **Told today's date**, for the same relative-time reasoning Stage 4b needs.
- **A blank answer is treated as a failure**, not a valid empty response — if the model ever
  returns nothing (rare, but it happens), the system falls back to an honest message instead of
  showing nothing to the user.

---

## Pipeline-wide special cases (checked before any of the above)

- **Multiple tracking IDs mentioned at once** → the pipeline declines up front and asks which one,
  rather than silently picking the first and risking a wrong answer.
- **The full "thinking" trace** (every stage above, streamed live) **is a privilege** — only
  support/ops/admin roles see it; everyone else just gets the final answer. This is what makes
  the pipeline demoable and debuggable without exposing internals to end users.
