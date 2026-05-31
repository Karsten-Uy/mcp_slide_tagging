# Handoff to `slide_tagging`: producing decks for the new raw-`.pptx` endpoints

**Audience:** the `slide_tagging` (producer) team.
**Author:** the `mcp_slide_tagging` (consumer / server) side.
**Status:** action required — the current corpus output breaks the server's new gate.

The MCP server added a raw-slide endpoint and now enforces a stricter contract on the
corpus it serves. Three of the four existing decks fail that contract because of how
they were tagged. This doc specifies exactly what the producer must emit so decks load
and serve correctly. Copy it into `slide_tagging/docs/` if useful.

> For the consumer-side design, see [`ARCHITECTURE.md`](ARCHITECTURE.md) and
> [`SCALING.md`](SCALING.md). The relevant server code is
> [`src/poc_corpus.py`](../src/poc_corpus.py) (`load`, `_resolve_source_pptx`,
> `get_slide_pptx`) and [`src/manifest.py`](../src/manifest.py).

---

## 1. What changed on the server (why this handoff exists)

The server now exposes **`get_slide_pptx(deck, index)`** — it returns the *real* slide
as a standalone one-slide `.pptx` so the generating agent clones the actual shapes
(geometry, fills, fonts, charts) instead of rebuilding from the tag summary. Much higher
fidelity, but it requires the **source `.pptx`** to be available to the server.

To guarantee every served deck is actually clonable, the server enforces a
**deck-admission gate at load time** ([`Corpus.load`](../src/poc_corpus.py)):

> A deck is loaded **only if** a matching source `.pptx` exists **and** its slide count
> equals the tagged JSON's. Decks that fail are dropped from the **entire** corpus —
> not just `get_slide_pptx`, but `list_decks`, `search_slides`, `get_deck`, everything.

This is intentional (the consumer wants "properly tagged **and** has an associated PPT"
as a single unit). The consequence: **a deck that doesn't meet the contract becomes
invisible.** Today that silently emptied a deploy.

---

## 2. The contract the server enforces (the spec to satisfy)

For each tagged deck the producer hands off, all of the following must hold:

### 2.1 A source `.pptx` must exist and be resolvable
The server resolves the deck's `.pptx` from `SOURCE_PPTX_PATH` by, in order:
1. `source_filename` with its extension swapped to `.pptx` (so `foo.pdf` → `foo.pptx`),
2. else `<deck_id>.pptx`, where `deck_id` = the JSON filename minus `.tagged.json`.

**Implication:** the `.pptx` filename stem must match either `source_filename`'s stem or
the deck-id. The cleanest, least-ambiguous rule: **name the `.pptx` exactly
`<deck_id>.pptx`** (see §5).

### 2.2 The slide count must align — three numbers must agree
Let `N = len(Presentation(deck.pptx).slides)` (python-pptx, sldIdLst order). Then:

```
len(tagged_json["slides"])  ==  N
tagged_json["slide_count"]  ==  N        # the DeckTag.slide_count field
tagged_json["deck_length"]  ==  N        # currently mirrors slide_count
```

The server checks `len(slides[]) == N`. The other two must match for internal
consistency (today they don't — see §3).

### 2.3 Slide indices must be the python-pptx slide indices
`get_slide_pptx(deck, index)` slices the `.pptx` by `index` (0-based, sldIdLst order).
So **`slides[k].index` must equal the kth `.pptx` slide's position.** No slides skipped,
merged, reordered, or de-duplicated during tagging — index `k` in the tags must be
slide `k` in the file. If the tagger ever drops hidden/backup slides, the contract
breaks even when counts happen to match.

---

## 3. Root cause of the current breakage (concrete)

The shipped corpus was tagged from **PDF exports**, and PDF page count ≠ `.pptx` slide
count:

| Deck | `source_format` | `slide_count` field | `len(slides[])` | actual `.pptx` slides | Loads? |
|---|---|---|---|---|---|
| nigeria-economic-outlook | **pptx** | 29 | 29 | 29 | ✅ |
| digital-auto-report-2023 | pdf | 40 | 40 | 40 | ✅ (pdf pages happened to equal pptx) |
| electric-vehicle-sales-review | pdf | 25 | 25 | **26** | ❌ off-by-one |
| ereadiness-study-2023 | pdf | **81** | **33** | **83** | ❌ three different numbers |

Two distinct defects:
1. **Tagged from the wrong artifact.** `source_format: pdf` means the structural pass
   counted PDF pages, which drift from `.pptx` slides (animation steps, merged builds,
   trimmed appendices). Only `nigeria` was tagged from the `.pptx`, and only it (plus a
   lucky `digital-auto`) aligns.
2. **Internal inconsistency.** `ereadiness` has `slide_count=81` but only 33 entries in
   `slides[]` and 83 in the file — the producer emitted disagreeing counts within one
   record.

---

## 4. Required producer changes

Priority: **P0** blocks decks from loading; **P1** prevents silent breakage; **P2** is
ergonomics that mature the handoff.

### P0-1 — Tag from the `.pptx`, set `source_format: pptx`
Run Pipeline A (`pptx_parser`/structural extraction) on the **`.pptx`** itself so
`slide_count`, `len(slides[])`, and the indices all derive from the file the server
serves. Retire PDF-derived tagging for any deck destined for the corpus. (PDF parsing
was already a deferred/`.pptx`-only path per [ARCHITECTURE §4.6](ARCHITECTURE.md) — this
makes it a hard requirement for served decks.)

### P0-2 — Make the three counts agree
Ensure `slide_count == deck_length == len(slides[]) == python-pptx slide count`. Today
`blank_tag` mirrors `slide_count` into `deck_length` from `DeckStructural.slide_count`
([`schema/tagged.py`](../../slide_tagging/src/slide_tagger/schema/tagged.py)); the bug is
when `slides[]` is later trimmed/hand-edited without updating the counts. One slide tag
per `.pptx` slide, always.

### P0-3 — Emit the `.pptx` as a first-class deliverable
The handoff currently produces JSON + asset PNGs only. It must also surface the source
`.pptx` for the deck, named per §5, into (or alongside) the hand-off folder so the
consumer's snapshot can include it. A copy is fine; don't rely on the consumer hunting
for it in `data/source/` under a different name (e.g. `ereadiness-study-2023` vs the
real `strategyand-ereadiness-study-2023.pptx`).

### P1-1 — Extend `validate` to assert the contract
Add to the existing `validate` command an alignment check that fails when:
- the resolved `.pptx` is missing,
- `python-pptx slide count != len(slides[])` (or `!= slide_count`/`deck_length`),
- slide indices aren't contiguous `0..N-1`,
- `source_format != "pptx"` for a served deck.

This catches misalignment **at production time**, before it reaches the server and
silently empties a deploy.

### P1-2 — Reconcile the two broken decks
- `electric-vehicle-sales-review-q4-2022`: re-tag the 26-slide `.pptx` (currently 25).
- `ereadiness-study-2023`: re-tag `strategyand-ereadiness-study-2023.pptx` (83 slides);
  the current 33/81 record can't be reconciled to that file — it was a different/trimmed
  source. Decide which `.pptx` is canonical and tag *that one*.

### P2-1 — Emit the manifest (the producer knows the counts already)
The server reads a `manifest.json` mapping `pptx_filename -> slide_count` so its boot-time
gate is a dict lookup instead of opening every `.pptx` (matters as the corpus grows). The
producer is the natural author — it has the count at tag time. Emit it as part of the
export so the consumer doesn't run a separate `build_manifest` step. Format in §6.

### P2-2 — A `bundle`/`export` command
Add a producer command that assembles the deploy snapshot deterministically: tagged JSON
+ assets + the `.pptx` (named per §5) + `manifest.json`, optionally filtering by
`confidentiality_tier`. This turns the §"folder copy" handoff into a versioned bundle and
is the foundation for incremental re-ingest later.

---

## 5. Naming & resolution spec (exact)

| Artifact | Required name | Resolved by server as |
|---|---|---|
| Tagged JSON | `<deck_id>.tagged.json` | `deck_id` = filename minus `.tagged.json` |
| Source `.pptx` | **`<deck_id>.pptx`** (recommended) | `Path(source_filename).with_suffix(".pptx").name`, else `<deck_id>.pptx` |
| Asset PNGs | `assets/<deck_id>/*.png` | via `recurring_elements[].image_path` |

Recommendation: **name the `.pptx` `<deck_id>.pptx`.** It removes the dependency on
`source_filename` (which legitimately points at the original `.pdf`/oddly-named file) and
makes resolution deterministic. If you instead keep an arbitrary `source_filename`,
ensure its stem matches the `.pptx` stem.

---

## 6. Manifest format (`manifest.json`)

Lives next to the source `.pptx` files (`SOURCE_PPTX_PATH/manifest.json`). Keyed by
`.pptx` filename; no deck/corpus knowledge required, so the producer can emit it by
scanning its `.pptx` outputs:

```json
{
  "version": 1,
  "slide_counts": {
    "nigeria-economic-outlook-october-2023-v1.pptx": 29,
    "digital-auto-report-2023.pptx": 40
  }
}
```

The count must equal `len(Presentation(file).slides)` (python-pptx). The server falls
back to parsing any `.pptx` the manifest omits, so a partial manifest is safe — but a
*wrong* count will wrongly admit/exclude a deck, so generate it from python-pptx, not by
hand. Reference generator: [`src/manifest.py`](../src/manifest.py) /
[`scripts/build_manifest.py`](../scripts/build_manifest.py).

---

## 7. Acceptance criteria (definition of done per deck)

A deck is "deploy-ready" when:
- [ ] `source_format == "pptx"` and the `.pptx` is included in the handoff, named `<deck_id>.pptx`.
- [ ] `python-pptx slide count == len(slides[]) == slide_count == deck_length`.
- [ ] Slide indices are contiguous `0..N-1` and map 1:1 to the `.pptx` slides in order.
- [ ] `validate` passes the new alignment check (P1-1).
- [ ] `manifest.json` includes the deck with the correct count.
- [ ] `confidentiality_tier` is set (the consumer serves raw `.pptx` on a public,
      no-auth endpoint today — flag anything restricted so the bundle step can exclude it).

When all decks in a handoff meet this, the consumer commits the snapshot and redeploys,
and every deck appears in every endpoint **with** working raw-slide cloning.

---

## 8. Quick reference — the server's resolution & gate (for parity)

From [`src/poc_corpus.py`](../src/poc_corpus.py), so the producer can mirror the logic:

```python
# resolution
candidates = []
if source_filename:
    candidates.append(Path(source_filename).with_suffix(".pptx").name)
candidates.append(f"{deck_id}.pptx")
src = first candidate that exists under SOURCE_PPTX_PATH   # else deck excluded

# admission gate
tagged = len(deck_json["slides"])
actual = manifest.get(src.name) or len(Presentation(src).slides)
load the deck  iff  tagged == actual                       # else excluded + logged
```
