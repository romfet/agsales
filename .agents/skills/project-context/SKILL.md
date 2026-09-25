---
name: project-context
description: Maintain and use the agsales project knowledge base from emails, chats, meeting transcripts, diagrams, screenshots, and other project materials. Use when adding project context, extracting requirements or decisions, reconciling conflicting information, or answering work questions that depend on accumulated business context. Do not use for ordinary code changes that can be completed from the code and existing technical documentation alone.
---

# Project Context

Maintain project knowledge under `docs/project-context/` without treating the skill itself as the knowledge store.

## Select the mode

- **Ingest:** The user provides or points to new correspondence, a transcript, an image, a diagram, or another source.
- **Retrieve:** A task depends on project history, business requirements, stakeholder intent, or prior decisions.
- **Reconcile:** New information conflicts with an existing source or summary.

## Ingest

Read [references/ingestion-format.md](references/ingestion-format.md), then:

1. Preserve a safe copy of the original material when practical. Put shareable sources in `docs/project-context/sources/`; put sensitive or local-only originals in the gitignored `docs/project-context/local-sources/`. Never store credentials or secrets; replace them with `[REDACTED]` and record that redaction occurred.
2. Create one normalized Markdown source note using the source-note template. Keep facts, direct stakeholder statements, decisions, proposals, open questions, and agent inference distinct.
3. Assign a stable source ID and link the note to the original and relevant project documents.
4. Update `docs/project-context/INDEX.md`. Update `PROJECT_STATE.md` only with durable information supported by a source; do not promote a proposal or unresolved discussion to current truth.
5. If the source contains an explicit, durable decision, create or update a decision record from the decision template. Preserve superseded decisions and link both directions.

## Retrieve

1. Read `docs/project-context/INDEX.md` and `PROJECT_STATE.md`.
2. Search the index, normalized notes, decisions, and existing `docs/` files for the task's terms and synonyms.
3. Load only the relevant sources. Prefer current approved decisions over proposals, and primary sources over summaries.
4. State uncertainty and cite source IDs or repository paths when project context materially affects the answer or implementation.

## Reconcile

Do not silently overwrite conflicting information. Record the conflict in both the new note and the index, identify the competing sources and their dates/authority, and mark the matter unresolved unless a newer authoritative source explicitly supersedes the older one. Ask for confirmation when the conflict would materially change implementation.

## Boundaries

- The code and tests describe current implemented behavior; context documents describe intent and agreements. Report discrepancies instead of assuming either is correct.
- Treat emails and chats as evidence, not automatically as approved requirements.
- Keep `AGENTS.md` and `PROJECT_STATE.md` compact. Detailed history belongs in source notes and decision records.
- Do not fabricate missing dates, participants, approvals, or conclusions. Use `unknown` and record the uncertainty.
