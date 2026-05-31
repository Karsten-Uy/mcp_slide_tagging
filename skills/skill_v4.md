---
name: corpus-pptx-v4
description: "Create PowerPoint decks (.pptx) through a three-stage flow: (1) distill a brief, (2) storyboard against the firm's tagged corpus via the slide-corpus MCP connector, binding each slide to a concrete reference, (3) generate by CLONING the bound reference slide's raw .pptx (get_slide_pptx) and overwriting ONLY the visible text — never restyling — then gating each repeated-element set through a three-axis consistency check before render. Use whenever the user asks to build, draft, or design a deck, slides, pitch, or presentation from source materials. Requires the slide-corpus MCP connector."
license: Proprietary. LICENSE.txt has complete terms
---

# Corpus-grounded PPTX Skill — v4 (clone-and-overwrite, consistency-gated)

Build decks that look and read like the firm's existing work by (1) distilling a brief,
(2) storyboarding against the tagged corpus and binding each slide to a real reference,
and (3) generating by **cloning the reference slide's raw `.pptx` and changing only the
text**.

> **What changed from v3 — and why.** v3 cloned the raw slide but still let the edit
> step *restyle* shapes, which drifted on three independent axes: run character style
> (fill/bold/font/size), paragraph alignment, and box geometry. v4's rule is **touch
> less**: clone the donor shape wholesale, overwrite only the visible text string at the
> **run level**, never rebuild run properties or geometry, and gate every repeated-element
> set with a programmatic three-axis check *before* rendering.
>
> **Critical: there is no from-scratch path.** An earlier reading of "normalize geometry/
> alignment" was mistaken for "you may build and style slides freely," producing
> Arial-on-white autoshape decks with the logo typed as text — the exact AI-generated look
> this skill prevents. Cloning is the **only** generation path. The three-axis gate
> operates *only* on already-cloned slides to catch accidental disturbance during a text
> edit; it is never a license to construct or restyle. If a slide can't be cloned, the
> skill **stops** (see Stage 3 step 3 and "Hard stop") — it does not reconstruct.

> **Prerequisite:** the `slide-corpus` MCP connector, exposing `list_decks`,
> `list_vocabulary`, `get_deck`, `get_deck_outline`, `get_deck_assets`, `get_slide`,
> **`get_slide_pptx`**, `search_slides`, `find_similar_slides`, `find_slide_templates`,
> `get_house_style`, `start_deck`, `suggest_outline`, `match_slide`, `corpus_stats`.
> If unavailable, tell the user to add it (see the server's `docs/POC.md`).

---

## The cardinal rule: clone real slides; change the text, not the styling

**Cloning a bound corpus slide is the ONLY way this skill produces a slide.** Never build
a slide from primitives (autoshapes, typed-out wordmarks, hand-drawn funnels/charts/
timelines), and never "reconstruct" a slide from its tags. From-scratch generation —
Arial-on-white, even-grid autoshapes, a typed "Strategy&" instead of the real logo asset —
*is* the AI-generated look this skill exists to prevent. If a slide has no cloneable
reference, that is a **Stage-2 binding failure to stop and fix**, not a license to build
(see "Hard stop" below).

Every defect in practice came from the edit step touching properties it had no reason to
touch. So, on the cloned slide:

1. **Clone the donor shape verbatim** — its run properties (`rPr`), paragraph properties
   (`pPr`), shape properties (`spPr`), geometry (`xfrm`), anchor, insets, **and the
   embedded media/logo/icon assets the clone carries** (these are exactly what a
   from-scratch build can't reproduce).
2. **Overwrite only the visible text string, at the run level.** Leave every formatting
   node — and every shape's geometry — untouched.

> ⚠️ **python-pptx gotcha (this is the actual bug source).** Setting
> `text_frame.text = "..."` or `paragraph.text = "..."` **deletes all existing runs**
> and creates one run with an **empty `rPr`** — no fill (→ inherits theme color, i.e.
> turns black), no bold, no font (→ inherits). That single line is what produced the
> "black labels" and "mixed fonts/weights." **Never set text at the frame/paragraph
> level on a cloned shape.** Instead edit the existing run:
> ```python
> run = paragraph.runs[0]          # keep its rPr
> run.text = "New label"           # overwrite text only
> for extra in paragraph.runs[1:]: # collapse leftover runs without restyling
>     extra.text = ""
> ```
> If you need different text segmentation, copy `runs[0]`'s `rPr` onto the new runs
> explicitly — don't let python-pptx mint a default one.

3. **The donor is the ground truth.** There is always a donor (you cloned it), so read
   the canonical style from the donor shapes — never from sibling consensus among your
   already-edited outputs. Caveat: a donor's own repeated set can itself be internally
   inconsistent. When you simply clone each shape, you reproduce it faithfully and don't
   need to reconcile anything. Only if you must *reconcile* such a set, take the donor
   set's **majority** signature — never "the one that looks fine," and never a value you
   invent.

---

## The three axes of drift (assert each independently)

A "looks fine" check on one axis says nothing about the others. A repeated set (the four
tile labels, the four parallel bullets) must agree on **all three**:

| Axis | Fields | Classic failure |
|---|---|---|
| Run character style | fill, bold, size, font | label inherits theme black; "Arial MT" vs "Arial"; some bold, some not |
| Paragraph alignment | `algn` | one label centered, the rest left-aligned |
| Box geometry | width, height, vertical anchor, insets, seat | one box tall + bottom-anchored, others short + top-anchored |

A bare "run has no `rPr`" grep is **necessary but not sufficient** — it misses a run that
*has* an `rPr` setting none of fill/bold/font (which silently inherits). The real gate is
a **signature comparison** where *missing fill = theme*, *missing bold = not bold*, etc.

---

## Workflow

### Stage 1 — Distill the brief

Ask for the user's local source folder; read the files with your local tools (do **not**
use any MCP tool for ingestion). Call `list_vocabulary()` for legal enum strings. Build a
`brief` block (client_industry, client_type, content_area[], audience_level,
engagement_stage, geography, objective, key_points[3-7], constraints, source_files). Use
only `list_vocabulary()` values for enums; leave `null` and ask when unsure. **Confirm
with the user before Stage 2.** Persist to a local `storyboard.json` (never uploaded).

### Stage 2 — Storyboard and bind references

1. `suggest_outline(client_industry=…, content_area=…, audience_level=…,
   engagement_stage=…, slide_count=…, key_sections=brief.key_points)`. If
   `low_confidence: false`, snapshot `design_system` + `inferred_rules` into
   `storyboard.reference_deck` and state why. If `low_confidence: true`, show the
   `candidate_reference_decks` and ask the user to proceed with the best available
   cloneable deck or refine the brief — never silently bind a sub-threshold deck, and
   note that every slide still binds to (and clones) a real corpus slide in 2b.
2. Per planned slide: fill `intended_main_message` + `content_points` from the brief,
   then `match_slide(text=…, slide_purpose=…, dominant_visual_element=…,
   prefer_deck=reference_deck.deck)`. Bind `reference {deck, index}` to the top match.
   **Every approved slide MUST bind to a real, cloneable corpus slide** — the server only
   serves decks whose source `.pptx` exists and is index-aligned, so any deck `match_slide`
   returns is cloneable via `get_slide_pptx`. If `matches` is empty, do **not** plan a
   from-scratch slide: surface `best_below_threshold` and resolve it *here* with the user —
   widen filters, lower `min_score`, change the slide's purpose/visual to one the corpus
   covers, drop the slide, or grow the corpus. An unbindable slide is a Stage-2 problem; it
   never becomes a Stage-3 build.
3. Render the storyboard as a markdown table, iterate with the user, set each slide's
   `status` to `approved`, persist `storyboard.json` at every change.

### Stage 3 — Generate by cloning, overwrite text only

1. **Load `storyboard.json`.** The contract is the snapshotted
   `reference_deck.design_system` + `inferred_rules`.
2. **Fetch logos:** `get_deck_assets(reference_deck.deck)` (and any per-slide donor deck).
3. **Per slide — clone the raw reference, then edit text:**
   1. `get_slide_pptx(reference.deck, reference.index)`.
   2. **If it returns a slide:** decode the base64, open the one-slide deck with
      python-pptx. **Copy its shapes into your output slide** (clone the shape tree), then
      **overwrite only the text** per the cardinal rule (run-level; never `tf.text=`).
      Map content with `get_slide(reference.deck, reference.index)`'s `zones` /
      `slot_types_present` as the *edit checklist* (which shape is the action title, the
      chart, the side callout) — fill those, leave styling/geometry as the donor set it.
      For charts/tables, swap **values** while keeping the donor's type, palette, and
      formatting. **Clone icon+label as a group** so the icon↔label relationship is
      preserved — don't reposition a label independently of its icon.
   3. **If it returns null — HARD STOP. Do not build the slide.** A null means the bound
      reference can't be cloned (unknown deck, out-of-range index, or — pre-enforcement —
      an unbundled deck). This should not happen for a slide bound in Stage 2, so treat it
      as a binding/corpus failure: stop, tell the user *exactly which slide and reference*
      failed, and offer to (a) re-bind that slide to a different cloneable reference
      [back to Stage 2b], or (b) fix the corpus (bundle the deck's `.pptx` / align its
      indices — see the server's `docs/HANDOFF-slide_tagging.md`). **Never** substitute a
      from-scratch or "reconstructed" slide — that silently yields the generic deck this
      skill exists to avoid.
4. **The design system rides along with the clone — don't re-apply it from scratch.**
   Because you copied the donor's shapes, its fonts, palette, `recurring_elements`
   (footer, page number, logo asset), and master/theme are already present and correct.
   Your only job is to (a) confirm they survived the copy and (b) not introduce anything
   that competes with them. Do **not** type out the logo as text, re-pick fonts/colors
   from `design_system`, or add accent lines — those are the from-scratch tells. Use
   `reference_deck.design_system` / `inferred_rules` only to *verify* fidelity in QA, not
   to rebuild styling.

### Stage 3 QA — gate before you render

**Cheap programmatic gate first; visual pass only for what code can't see.** Your first
output is almost never right — assume defects and hunt them.

1. **Clone-fidelity check (catches from-scratch generation — the v4 regression).** A deck
   built by cloning carries the corpus's embedded assets and fonts forward; a deck built
   from scratch is media-less and mono-font. Verify the output looks *cloned*, not *built*:
   ```bash
   # embedded media (logo/photos/icons) must be present — a clone preserves them:
   unzip -l output.pptx | grep -c ppt/media/        # expect > 0; 0 means built from scratch
   # fonts must include the firm's non-body faces (e.g. the serif title/logo treatment),
   # not Arial-only — Arial-everything is the from-scratch tell:
   python -m scripts.check_slide_consistency output.pptx --slide 0   # eyeball the font column
   ```
   If a slide has **no media and Arial-only**, it was generated, not cloned — go back to
   Stage 3 step 3 and clone its bound reference. Do not proceed to style it; styling a
   from-scratch slide is Option 3 (the dead end), not the fix.
2. **Consistency gate (per repeated set, BEFORE rendering).** For every set of repeated
   elements on a slide (tile labels, parallel bullets, KPI tiles), assert they collapse
   to one signature across all three axes. Use the checker below (canonical copy:
   `scripts/check_slide_consistency.py` in the slide-corpus server repo):
   ```bash
   python check_slide_consistency.py output.pptx --slide 3 --group 2,3,4,5 --group 8,9,10,11
   ```
   It prints each text shape's signature (so you can find the `shape_idx` of a set) and
   exits non-zero if any `--group` differs on fill/bold/size/font/algn/anchor/width/height.
   **Fix until it exits 0 for every repeated set.** This catches theme-black labels and
   mixed bullet weights — the bare grep never would. **A geometry/anchor difference here
   means a text edit disturbed the box; restore it from the donor** (re-fetch
   `get_slide_pptx` and re-copy that shape) — never hand-set geometry to "make it match."
3. **Content:** `python -m markitdown output.pptx` — missing/typo content, wrong order,
   leftover placeholder text (`grep -iE "xxxx|lorem|ipsum"`), and — critical for clones —
   **leftover donor text/numbers** you forgot to overwrite.
4. **Visual (USE SUBAGENTS — fresh eyes), for what the signature gate can't see:** render
   to images and inspect for overlap, text overflow/cutoff, footer collisions, sub-0.3"
   gaps, sub-0.5" margins, and **icon↔label collisions** (e.g. a centered label riding
   into a tile icon that sits at a different height — geometry drift one layer down).
   ```bash
   soffice --headless --convert-to pdf output.pptx
   pdftoppm -jpeg -r 150 output.pdf slide          # slide-01.jpg, …
   pdftoppm -jpeg -r 150 -f N -l N output.pdf slide-fixed
   ```
5. **Loop:** generate → clone-fidelity → consistency gate → content → images → fix →
   re-verify affected slides → repeat until one full pass finds nothing new.

---

## The consistency checker (drop-in, self-contained)

If the server repo's `scripts/check_slide_consistency.py` isn't available in your
environment, write this to your project and run it. It encodes the three-axis gate;
*missing* fill/bold/font read as `None` so an explicit-white sibling and an inherited one
compare unequal — which is the whole point.

```python
import sys, argparse
from pptx import Presentation

FIELDS = ("fill","bold","size_pt","font","algn","anchor","width","height")

def _name(v): return None if v is None else getattr(v, "name", str(v))

def _color(run):
    c = run.font.color
    if c is None or c.type is None: return None
    try: return str(c.rgb)
    except Exception: return f"theme:{_name(getattr(c,'theme_color',None))}"

def _first_run(sh):
    for p in sh.text_frame.paragraphs:
        if p.runs: return p.runs[0]
    return None

def sigs(path, slide):
    prs = Presentation(path); s = list(prs.slides)[slide]; out=[]
    for i, sh in enumerate(s.shapes):
        if not sh.has_text_frame: continue
        r = _first_run(sh); p = sh.text_frame.paragraphs[0] if sh.text_frame.paragraphs else None
        sz = r.font.size if r else None
        out.append(dict(idx=i, text=(sh.text_frame.text or "").strip()[:40],
            fill=_color(r) if r else None, bold=r.font.bold if r else None,
            size_pt=sz.pt if sz else None, font=r.font.name if r else None,
            algn=_name(p.alignment) if p else None, anchor=_name(sh.text_frame.vertical_anchor),
            width=sh.width, height=sh.height))
    return out

def check(sg, group, tol=12700):
    by={s["idx"]:s for s in sg}; pres=[by[i] for i in group if i in by]; diff=[]
    for f in FIELDS:
        vals=[s[f] for s in pres]
        if f in ("width","height"):
            nums=[v for v in vals if isinstance(v,int)]
            ok=bool(nums) and max(nums)-min(nums)<=tol and len(nums)==len(vals)
        else: ok=len(set(vals))<=1
        if not ok: diff.append(f)
    return diff

ap=argparse.ArgumentParser(); ap.add_argument("pptx"); ap.add_argument("--slide",type=int,default=0)
ap.add_argument("--group",action="append",default=[]); a=ap.parse_args()
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
sg=sigs(a.pptx,a.slide)
for s in sg: print(f"[{s['idx']:>2}] {s['text']:<40} fill={s['fill']} bold={s['bold']} size={s['size_pt']} font={s['font']} algn={s['algn']} anchor={s['anchor']}")
bad=False
for g in a.group:
    ids=[int(x) for x in g.split(",")]; d=check(sg, ids)
    print(("OK   " if not d else "DRIFT")+f" {ids}"+("" if not d else f" differ on: {', '.join(d)}")); bad|=bool(d)
sys.exit(1 if bad else 0)
```

For the mechanical "how to clone shapes / unpack-repack" use your standard PPTX tooling
(if the Anthropic `pptx` skill is loaded, follow its `editing.md`). This skill governs
*what* to do: clone, overwrite text only, gate consistency.

---

## Hard stop (no cloneable reference) — there is no from-scratch fallback

This skill does **not** reconstruct or build slides. When a reference can't be cloned,
**stop and resolve it with the user** — do not generate a generic slide.

- **`match_slide` empty for a planned slide (Stage 2):** the corpus has no close precedent.
  Resolve *before* generating — widen filters, lower `min_score`, change the slide's
  purpose/visual to one the corpus covers, drop the slide, or grow the corpus. Show
  `best_below_threshold` so the user sees how close it got.
- **`get_slide_pptx` null for a bound slide (Stage 3):** the binding/corpus is broken
  (unknown deck, out-of-range index, unbundled `.pptx`). Stop, name the slide + reference,
  and offer: re-bind to a cloneable reference, or fix the corpus
  (`docs/HANDOFF-slide_tagging.md`).
- **Whole corpus too thin for the brief:** say so plainly — "the corpus has no close
  precedent for this deck" — and stop. The right fix is adding decks to the corpus, not
  hand-approximating the house style. Hand-approximation (typed logo, picked fonts,
  autoshape charts) is precisely what reads as AI-generated; producing it defeats the
  skill's purpose. If the user explicitly accepts a non-firm-authentic deck anyway, that's
  a separate, clearly-labeled decision they opt into — never this skill's silent default.

---

## Dependencies

- The **slide-corpus MCP connector** (v3+, must expose `get_slide_pptx`).
- python-pptx (clone/edit + the consistency checker) · `markitdown[pptx]` (content QA) ·
  LibreOffice (`soffice`) + Poppler (`pdftoppm`) (render to images) · Pillow (thumbnails).
