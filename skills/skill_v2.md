---
name: corpus-pptx-v2
description: "Create PowerPoint decks (.pptx) through a three-stage flow: (1) collect & summarize the user's local source materials into a structured brief, (2) draft an iterative storyboard with the user — each planned slide bound to a concrete reference slide from the firm's tagged corpus via the slide-corpus MCP connector, (3) generate the .pptx from the approved storyboard. Use whenever the user asks to build, draft, or design a deck, slides, pitch, or presentation from source materials (RFP + supporting files). Requires the slide-corpus MCP connector to be enabled."
license: Proprietary. LICENSE.txt has complete terms
---

# Corpus-grounded PPTX Skill — 3-Stage Flow

Build decks that look and read like the firm's existing work — not generic AI slides
— by running a three-stage flow:

1. **Collect & summarize** the user's source materials (RFP + supporting files in a
   local folder) into a structured brief.
2. **Storyboard** the deck with the user — pick the closest reference deck for the
   design system, plan each slide, **bind each slide to a concrete reference slide
   from the corpus** to clone-and-edit, iterate back-and-forth, write a
   `storyboard.json`.
3. **Generate** the `.pptx` from the approved storyboard. The binding is already
   done; this stage is the mechanical build + QA against the bound references.

The corpus is the source of truth for *how slides should look and be structured*.
This skill's own design suggestions are a **fallback only**, used when
`suggest_outline` / `match_slide` return weak or empty results.

> **Prerequisite:** the `slide-corpus` MCP connector must be enabled, exposing
> these tools: `list_decks`, `list_vocabulary`, `get_deck`, `get_deck_outline`,
> `get_deck_assets`, `get_slide`, `search_slides`, `find_similar_slides`,
> `find_slide_templates`, `get_house_style`, `start_deck`, **`suggest_outline`**,
> **`match_slide`**, `corpus_stats`. If they aren't available, tell the user to
> add the connector (see the server's `docs/POC.md`) before proceeding.

---

## The storyboard artifact (`storyboard.json`)

A single canonical JSON file kept in the user's **local project folder**, never
uploaded. JSON is the source of truth (Stage 3 consumes it deterministically); for
each review round you also **render it as a markdown table** for the user, apply
their edits, and write the JSON back.

Schema (`schema_version: "storyboard/v1"`):

```jsonc
{
  "schema_version": "storyboard/v1",
  "project": "acme-fx-board-update",

  // Stage 1 — structured brief
  "brief": {
    "client_industry": "Financial Services",   // value from list_vocabulary()
    "client_type": "Private F500",
    "content_area": ["Risk", "Market analysis"],
    "audience_level": "C-suite / board",
    "engagement_stage": "Mid-project readout",
    "geography": "EMEA",
    "objective": "...",
    "key_points": ["...", "..."],
    "constraints": ["<= 8 slides", "..."],
    "source_files": ["rfp.pdf", "fx_data.xlsx"]
  },

  // Stage 2 — chosen design precedent (snapshotted at approval)
  "reference_deck": {
    "deck": "nigeria-economic-outlook-october-2023-v1",
    "why": "...",
    "deck_summary": "...",
    "design_system": { /* verbatim from get_deck(deck).design_system */ },
    "inferred_rules": { /* from get_deck(deck).inferred_rules */ },
    "recurring_assets_available": true
  },

  // Stage 2 — ordered plan
  "slides": [
    {
      "index": 0,
      "slide_purpose": "Title",
      "message_type": "Assertion",
      "slide_position_role": "Hero / headline",
      "dominant_visual_element": "Pure text",
      "intended_main_message": "...",
      "content_points": ["..."],
      "reference": {                          // bound proven slide to clone/edit
        "deck": "nigeria-economic-outlook-october-2023-v1", "index": 0,
        "why": "Same purpose + Hero/headline.",
        "reusability_score_qualitative": "High",
        "tier_match_difficulty": "Likely Tier 1 candidate"
      },
      "status": "approved"                    // draft | approved | needs_rework
    }
  ]
}
```

Legal **values** for enum fields (`slide_purpose`, `message_type`,
`slide_position_role`, `dominant_visual_element`, `client_industry`,
`content_area`, `audience_level`, `engagement_stage`, …) come from
`list_vocabulary()` at runtime — never invent them.

---

## Workflow

### Stage 1 — Collect & summarize source materials → `brief`

1. Ask the user for the **local folder path** holding the source materials (RFP +
   supporting files). Read every file in it with your local file tools
   (pdf/pptx/xlsx/docx/md) — this is local work; do **not** call any MCP server
   tool for ingestion. Cite which files you read.
2. Call `list_vocabulary()` once to get the legal enum strings.
3. Distill the materials into the `brief` block:
   - Enum fields (`client_industry`, `audience_level`, `engagement_stage`,
     `geography`, …) must use **only** values from `list_vocabulary()`;
     leave `null` and ask the user when you can't tell from the materials.
   - `content_area` is a list; pick all that apply.
   - `objective` = one-sentence purpose. `key_points` = the 3-7 most important
     points from the RFP (these become Stage-2 storyboard messages).
   - `constraints` = anything the user or RFP imposes (slide count, length,
     visual style, deadlines).
   - `source_files` = list filenames only (never inline file contents — the user
     can see them locally).
4. **Confirm the brief with the user before Stage 2.** State what you put in each
   field and what you're guessing. Do not move on until the user signs off.
5. Write the brief into `storyboard.json` (`brief` block).

### Stage 2 — Storyboard with the user → approved `storyboard.json`

#### 2a. Pick the reference deck (deterministic, thresholded)

Call `suggest_outline(client_industry=…, content_area=…, audience_level=…,
engagement_stage=…, slide_count=brief.constraints.slide_count or sensible default,
key_sections=brief.key_points)`.

- If `low_confidence: false` and `chosen_reference_deck` is set: snapshot
  `design_system` and `inferred_rules` into `storyboard.reference_deck` and state
  *why* this deck was chosen (echo `match_score` and the per-dimension `why`).
- If `low_confidence: true` (no deck cleared `min_deck_score=3.0`): show the user
  the `candidate_reference_decks` with their `match_score_normalized` and ask
  them whether to:
  (a) proceed with the weak best-match (then snapshot its design system anyway),
  (b) fall back to `get_house_style()` for a no-precedent design system, or
  (c) refine the brief (loop back to Stage 1).
  **Never silently bind to a sub-threshold deck.**

#### 2b. Draft the ordered slides

Take `suggest_outline`'s `slides[]` as the starting outline. For each planned
slide, refine the binding with the message-level threshold:

1. Fill `intended_main_message` from `brief.key_points` (and `content_points`
   from the same brief).
2. Call `match_slide(text=intended_main_message, slide_purpose=…,
   dominant_visual_element=…, prefer_deck=reference_deck.deck)`.
3. If `matches` is non-empty: bind `reference {deck, index}` to the top match;
   record its `score`, `why`, `reusability_score_qualitative`,
   `tier_match_difficulty`.
4. If `matches` is empty: show the user `best_below_threshold` so they see *how
   close* it got, then pick one of:
   (a) loosen filters and retry,
   (b) lower `min_score` and retry,
   (c) fall back to `find_slide_templates(slide_purpose=…)` for a
       structure-only reference (mark `low_confidence: true` on that slide), or
   (d) leave the binding blank pending user input.
   **Never silently bind a slide to a sub-threshold reference.**

#### 2c. Iterate with the user

Render the storyboard as a **markdown table** for review — surface any
`low_confidence: true` slides explicitly:

```
| # | Purpose | Role | Intended message | Visual | Reference (deck#idx) | Status |
|---|---|---|---|---|---|---|
| 0 | Title | Hero / headline | FX volatility forces… | Pure text | nigeria-…#0 | draft |
| … |
```

Apply the user's edits back into `storyboard.json`:
- **Reorder / remove / insert slides** → renumber `index`.
- **Swap a reference** → re-call `match_slide` with their hint and bind the new top.
- **Edit a message** → re-call `match_slide` (the binding may need updating).
- **Tighten / loosen thresholds** → re-call `suggest_outline` or `match_slide`
  with new `min_deck_score` / `min_score`.

Set each slide's `status` to `approved` once the user signs off; iterate until all
slides are `approved`. Persist `storyboard.json` at every change.

### Stage 3 — Generate the `.pptx` from the approved storyboard

The binding work is done. This stage is the mechanical build + QA.

1. **Load `storyboard.json`.** The design contract is the snapshotted
   `reference_deck.design_system` + `inferred_rules` — treat it as authoritative,
   *not* whatever the live corpus currently says.
2. **Fetch logos now.** Call `get_deck_assets(reference_deck.deck)`. If any
   slide's `reference.deck` differs from the header deck, also fetch
   `get_deck_assets(that deck)` for that slide's donor branding.
3. **Per slide**, call `get_slide(reference.deck, reference.index)` to get the
   full tags + `zones` + `slot_types_present`. **Build by cloning that
   structure**, filling `content_points` and `intended_main_message` into the
   locked design system. A "Finding with a chart" slide → chart + action title +
   side callout (matching the donor's zones); a "Framework" → the diagram; etc.
4. **Apply the design system uniformly:**
   - Theme = `reference_deck.design_system`. Map `title_style`/`body_style`
     fonts + sizes, and `color_palette` (primary 60–70%, accent for emphasis,
     neutrals for body) onto the slide master / theme.
   - Reproduce `recurring_elements` on every slide (footer text, page numbers,
     logo placement). To embed the logo: from `get_deck_assets`, pick the item
     with `type == "logo"`, `base64.b64decode(item["base64"])`, write it to a
     file, and place it with `slide.shapes.add_picture(path, left, top,
     height=…)` at the reported `position` (e.g. top-right); reuse the same
     file across slides and don't distort the aspect ratio. If no logo asset is
     returned, fall back to the footer text `value`.
   - Honor `inferred_rules` (e.g. if `title.uses_action_titles == "always"`,
     write action titles; if `chart_styling.uses_consistent_palette == "true"`,
     reuse the palette in charts).

For the mechanical "how to write the .pptx" (python-pptx / pptxgenjs, templates,
packing/unpacking), use your standard PPTX tooling; if the full Anthropic `pptx`
skill is loaded, follow its `editing.md` / `pptxgenjs.md`. This skill governs
*what* to build (storyboard-bound design + structure), not the file mechanics.

### Stage 3 QA — against BOUND references + the snapshotted design system

**Assume there are problems. Your job is to find them.** Your first render is
almost never correct.

- **Content:** `python -m markitdown output.pptx` — check for missing content,
  typos, wrong order, leftover placeholder text (`grep -iE "xxxx|lorem|ipsum"`).
- **Visual (USE SUBAGENTS — fresh eyes):** convert to images and inspect for
  overlap, text overflow/cutoff, footers colliding with content, sub-0.3" gaps,
  sub-0.5" margins, misaligned columns, low-contrast text/icons.
- **Structure match (new in v2):** for each generated slide, verify its
  structure matches its **bound reference's** `zones` and `slot_types_present`
  (re-fetch with `get_slide(reference.deck, reference.index)`).
- **Design fidelity match (new in v2):** fonts / palette / footer / logo match
  the **snapshotted `reference_deck.design_system`** and `inferred_rules`
  (e.g. action titles where `uses_action_titles == "always"`).
- **Loop:** generate → images → list issues → fix → re-verify affected slides →
  repeat. Don't declare success until one full fix-and-verify pass finds nothing
  new.

#### Converting to images
```bash
soffice --headless --convert-to pdf output.pptx
pdftoppm -jpeg -r 150 output.pdf slide        # slide-01.jpg, slide-02.jpg, …
pdftoppm -jpeg -r 150 -f N -l N output.pdf slide-fixed   # re-render one slide
```

---

## Example (a 3-stage trace)

Brief: *"Two-slide FX-risk update for a board, like our Nigeria outlook."*
User's local folder: `~/projects/acme-fx-update/` with `rfp.pdf` and `fx_data.xlsx`.

**Stage 1 — Collect & summarize.** Read `rfp.pdf` + `fx_data.xlsx`. Call
`list_vocabulary()`. Distill:

```json
{
  "client_industry": "Cross-industry",
  "content_area": ["Market analysis", "Risk"],
  "audience_level": "C-suite / board",
  "engagement_stage": "Mid-project readout",
  "geography": "EMEA",
  "objective": "Decide whether to hedge FX exposure for Q3.",
  "key_points": ["USD/NGN volatility up 40% QoQ", "Three hedging options"],
  "constraints": ["<= 2 slides"],
  "source_files": ["rfp.pdf", "fx_data.xlsx"]
}
```
Confirm with the user. Write into `storyboard.json`.

**Stage 2 — Storyboard.**
1. `suggest_outline(client_industry="Cross-industry", content_area="Market analysis",
   audience_level="C-suite / board", engagement_stage="Mid-project readout",
   slide_count=2, key_sections=["USD/NGN volatility up 40% QoQ",
   "Three hedging options"])` → picks
   `nigeria-economic-outlook-october-2023-v1` (match_score 8/9, above threshold);
   snapshot its `design_system` + `inferred_rules` into the header.
2. For each planned slide, call `match_slide` with the intended message +
   purpose + visual + `prefer_deck="nigeria-economic-outlook-october-2023-v1"`
   → bind `reference {deck, index}` to the top match.
3. Render the markdown table; user reorders one slide and renames a message;
   re-call `match_slide` for the changed slide; mark both `approved`; write
   `storyboard.json`.

**Stage 3 — Generate.** Load `storyboard.json`. Fetch
`get_deck_assets("nigeria-economic-outlook-october-2023-v1")` for logos. Per
slide, `get_slide(reference.deck, reference.index)` for full structure → build in
the locked Arial red/black system with the recurring footer
"Impact of Global Economic Trends…" and the logo; action titles per
`inferred_rules`. QA loop until clean.

---

## Fallback (when the corpus has no close precedent)

- **`suggest_outline` returns `low_confidence: true`** (no deck cleared
  `min_deck_score`): use `get_house_style()` for fonts/palette and
  `find_slide_templates(slide_purpose=…)` per planned slide for structure-only
  references. Tell the user explicitly *"no close precedent exists in the corpus
  for this brief"* and continue.
- **`match_slide` returns `matches: []`** for a slide (nothing cleared
  `min_score`): show the user the `best_below_threshold`, then either widen
  filters, lower `min_score`, fall back to `find_slide_templates` (mark
  `low_confidence: true` on the slide), or leave the binding blank pending user
  input.

When no precedent applies, fall back to general design judgment but **don't
create boring slides.** Every slide needs a visual element; one color dominates
(60–70%) with one accent; dark title/closing + light content; commit to one
repeated motif. Pick a topic-specific palette (not default blue) and an
interesting header/body font pairing. Title 36–44pt bold, body 14–16pt, left-align
body (center only titles), ≥0.5" margins, consistent 0.3–0.5" gaps. **Never** put
accent lines under titles (an AI-slide tell) or create text-only slides.

---

## Dependencies

- The **slide-corpus MCP connector** (required — the design/reference source).
  Must expose `suggest_outline` and `match_slide` (PoC server v2+).
- `pip install "markitdown[pptx]"` — text extraction · `pip install Pillow` —
  thumbnails · python-pptx or `npm i -g pptxgenjs` — building · LibreOffice
  (`soffice`) + Poppler (`pdftoppm`) — render to images for QA.
