# Context ingestion format

Use the repository templates in `docs/project-context/templates/`.

## Stable identifiers and filenames

Use `CTX-YYYYMMDD-short-slug` for source notes and `DEC-YYYYMMDD-short-slug` for decisions. If the actual date is unknown, use the ingestion date and set `source_date: unknown`. Add a numeric suffix on collision.

Store normalized notes by source type:

- email: `correspondence/`
- chat or messenger export: `correspondence/`
- meeting or audio transcript: `meetings/`
- diagram, screenshot, or photo: `requirements/` unless another category clearly fits
- formal requirement or specification: `requirements/`

## Evidence classification

Classify each extracted item as one of:

- `confirmed`: explicitly approved or stated as current by an authoritative participant or document;
- `proposed`: suggested but not approved;
- `discussed`: part of exploration without a decision;
- `inferred`: analyst interpretation, never presented as a stakeholder statement;
- `superseded`: previously valid but explicitly replaced.

Record the evidence path and source ID for confirmed items. When approval authority is unclear, use `discussed` or `proposed`.

## Processing by source type

### Email and chat

Preserve chronology, participants, quoted references, explicit asks, commitments, deadlines, and unanswered questions. Separate a sender's statement from an agreed decision.

### Transcript

Retain speaker labels and timestamps when available. Extract decisions, action items with owners, requirements, objections, and open questions. Mark unclear speech and uncertain attribution instead of guessing.

### Diagram or image

Preserve the original when safe. Record visible labels, relationships, boundaries, data flows, legends, and handwritten annotations. Distinguish literal OCR or visual observations from interpretation. Link the description back to the image.

### Existing document

Prefer linking the original repository document rather than copying it. Create a normalized note only when provenance, status, extracted decisions, or cross-links add useful information.

## Updating summaries

`INDEX.md` is the navigation and conflict register. `PROJECT_STATE.md` is a compact current-state briefing, not a transcript archive. Update the latter only when new evidence changes goals, scope, architecture, integrations, constraints, confirmed decisions, or major open questions.
