---
name: corpus-pptx
description: "Create PowerPoint decks (.pptx) that match a firm's real design system by grounding every deck in the tagged slide corpus served by the slide-corpus MCP connector. Use whenever the user asks to build, draft, or design a deck, slides, pitch, or presentation — query the corpus first (design system + reference slides), then build. Requires the slide-corpus MCP connector to be enabled."
license: Proprietary. LICENSE.txt has complete terms
---

# Corpus-grounded PPTX Skill

Build decks that look and read like the firm's existing work — not generic
AI slides — by retrieving the design system and reference slides from the
**slide-corpus MCP connector** before generating anything.

The corpus is the source of truth for *how slides should look and be structured*.
This skill's own design suggestions are a **fallback only**, used when the corpus
has no close match.

> **Prerequisite:** the `slide-corpus` MCP connector must be enabled (tools:
> `list_decks`, `get_deck`, `get_deck_assets`, `search_slides`, `get_slide`, `find_similar_slides`).
> If those tools aren't available, tell the user to add the connector (see the
> server's `docs/POC.md`) before proceeding.

---

## Workflow

### Step 1 — Ground the deck in the corpus (REQUIRED, via MCP)

Do this before writing any slide code. Skipping it defeats the point of the skill.

1. **Find the closest reference deck(s).** Call `list_decks()` and pick the deck(s)
   whose `client_industry` / `content_area` / `audience_level` / `client_type`
   best match the brief. State which you chose and why.

2. **Pull the design system.** Call `get_deck(<deck>)` for the chosen reference and
   treat its `design_system` as **authoritative**:
   - `title_style` / `body_style` → fonts, sizes, weights, colors, alignment.
   - `color_palette` → `primary` (dominant), `accent`, `neutrals`. Use *these* hex
     values, not the fallback palettes below.
   - `default_text_alignment` and `grid` → layout structure.
   - `recurring_elements` → reproduce logos / footers / page numbers / watermarks
     (use each element's `type` and text `value`). If `recurring_assets_available`
     is true (i.e. an element has an `image_path`), call **`get_deck_assets(<deck>)`**
     to fetch the actual logo/branding images as base64 — you'll embed them in Step 2.
   - `inferred_rules` → observed conventions (e.g. `title.uses_action_titles`,
     `chart_styling.uses_consistent_palette`, `layout_conventions.uses_master_template`).
     Follow them (e.g. if titles are "always" action titles, write action titles).

3. **Find a reference slide for each storyboard point.** For every slide you plan,
   retrieve a precedent and mirror its structure:
   - `search_slides(slide_purpose=…, message_type=…, dominant_visual_element=…,
     content_area=…, audience_level=…, text=…)` for exact tag matches.
   - `find_similar_slides(text=…)` when you only have the idea in words.
   - `get_slide(<deck>, <index>)` for the full tag set of a promising hit.
   Reuse the matched slide's `slide_purpose`, `message_type`,
   `dominant_visual_element`, and layout intent. Echo good `main_message` phrasing.

4. **Summarize the plan** before building: the chosen design system (fonts, palette,
   grid, recurring elements) and, per planned slide, its purpose + the reference
   slide it's modeled on.

### Step 2 — Build the deck from the retrieved design

Generate the `.pptx` applying the corpus design, not defaults:

- **Theme = the retrieved `design_system`.** Map `title_style`/`body_style` fonts +
  sizes, and `color_palette` (primary 60–70% weight, accent for emphasis, neutrals
  for body) onto your slide master / theme.
- **Reproduce `recurring_elements`** on every slide (footer text, page numbers,
  logo placement) so the deck reads as part of the same series. **To embed the
  firm's logo:** from `get_deck_assets(<deck>)`, pick the item with `type == "logo"`,
  `base64.b64decode(item["base64"])`, write it to a file, and place it with
  `slide.shapes.add_picture(path, left, top, height=…)` at the reported `position`
  (e.g. top-right); reuse the same file across slides and don't distort the aspect
  ratio. If no logo asset is returned, fall back to the footer text `value`.
- **Match each slide to its reference** — same `slide_purpose` and
  `dominant_visual_element` (a "Finding" with a chart → chart + action title + side
  callout; a "Framework" → the diagram; etc.).
- **Honor `inferred_rules`** (action titles, consistent chart palette, master-template
  usage).

For the mechanical "how to write the .pptx" (python-pptx / pptxgenjs, templates,
packing/unpacking), use your standard PPTX tooling; if the full Anthropic `pptx`
skill is also loaded, follow its `editing.md` / `pptxgenjs.md`. This skill governs
*what* to build (corpus-grounded design + structure), not the file mechanics.

### Step 3 — QA against the corpus

Render to images and verify (see [QA](#qa-required) below) **plus**:
- Fonts, palette, and footer/logo match the reference deck's `design_system`.
- The logo (if any) is present, correctly placed, and not stretched/distorted.
- Each slide's structure matches the reference slide it was modeled on.
- Titles follow the corpus convention (e.g. action titles if `uses_action_titles`
  is "always").

---

## Example (what the tool calls look like)

Brief: *"Two-slide FX-risk update for a board, like our Nigeria outlook."*

1. `list_decks()` → pick `nigeria-economic-outlook-october-2023-v1`
   (Cross-industry/Market analysis, C-suite / board).
2. `get_deck("nigeria-economic-outlook-october-2023-v1")` → palette
   `primary #C00000 / accent #D93953 / neutrals …`, Arial titles, footer
   "Impact of Global Economic Trends…", `uses_action_titles: always`.
3. `search_slides(slide_purpose="Finding", dominant_visual_element="Chart",
   content_area="Market analysis")` → model the data slide on a real Finding slide.
4. Build 2 slides in that red/black Arial system, action titles, recurring footer.

---

## Fallback design ideas (ONLY when the corpus has no close match)

If `list_decks` / `search_slides` return nothing relevant, fall back to general
design judgment. **Don't create boring slides.** Every slide needs a visual element;
one color dominates (60–70%) with one accent; dark title/closing + light content;
commit to one repeated motif. Pick a topic-specific palette (not default blue) and
an interesting header/body font pairing. Title 36–44pt bold, body 14–16pt,
left-align body (center only titles), ≥0.5" margins, consistent 0.3–0.5" gaps.
**Never** put accent lines under titles (an AI-slide tell) or create text-only slides.

---

## QA (Required)

**Assume there are problems. Your job is to find them.** Your first render is almost
never correct.

- **Content:** `python -m markitdown output.pptx` — check for missing content,
  typos, wrong order, leftover placeholder text (`grep -iE "xxxx|lorem|ipsum"`).
- **Visual (USE SUBAGENTS — fresh eyes):** convert to images and inspect for
  overlap, text overflow/cutoff, footers colliding with content, sub-0.3" gaps,
  sub-0.5" margins, misaligned columns, low-contrast text/icons, and **drift from
  the corpus design system** (wrong fonts/palette, missing footer/logo).
- **Loop:** generate → images → list issues → fix → re-verify affected slides →
  repeat. Don't declare success until one full fix-and-verify pass finds nothing new.

### Converting to images

```bash
soffice --headless --convert-to pdf output.pptx
pdftoppm -jpeg -r 150 output.pdf slide        # slide-01.jpg, slide-02.jpg, …
pdftoppm -jpeg -r 150 -f N -l N output.pdf slide-fixed   # re-render one slide
```

---

## Dependencies

- The **slide-corpus MCP connector** (required — the design/reference source).
- `pip install "markitdown[pptx]"` — text extraction · `pip install Pillow` —
  thumbnails · python-pptx or `npm i -g pptxgenjs` — building · LibreOffice
  (`soffice`) + Poppler (`pdftoppm`) — render to images for QA.
