# Security audit — `mcp_slide_tagging` + `slide_tagging`

A point-in-time security review of both sibling repos: the **MCP server** (consumer,
internet-facing) and the **tagging CLI** (producer, operator-run). It lists current
vulnerabilities with severity, location, impact, and remediation, then a prioritized
fix order. This is an audit document — nothing here has been changed in code yet.

> **Audit basis.** Manual source review of the security-sensitive paths (auth, file I/O,
> path handling, subprocess, deserialization, secrets, untrusted-input parsing) as of
> this review. No dynamic scanning or dependency-CVE scan was run — see
> [§ Recommended ongoing practices](#recommended-ongoing-practices).

## Threat model (who can do what)

| Surface | Reachable by | Can they write/upload? |
|---|---|---|
| **MCP server** (`src/poc_server.py`, deployed, no-auth HTTPS) | **anyone on the internet** with the URL | No — read-only retrieval tools over a bundled corpus |
| **Corpus snapshot** (`corpus/`) | whoever builds/commits the deploy (operator + git supply chain) | Yes — controls the tagged JSON the server trusts |
| **Tagging CLI** (`slide_tagger`) | the operator running it locally / in CI | Yes — ingests arbitrary `.pptx`/`.pdf`, runs LibreOffice, calls Anthropic |

The two highest-value assets are **(a) the raw client decks** (real, potentially
confidential `.pptx`) and **(b) the API keys / DB credentials**. The MCP server's
attack surface is **data exfiltration + denial of service** (it never accepts uploads,
so no RCE there); the tagging CLI's surface is **untrusted-document processing** (RCE in
the parsers/converters it drives) and **data egress**.

---

## Findings

Severity uses likelihood × impact in this deployment. IDs are stable for tracking.

### H-1 · No-auth public endpoint exposes the full raw corpus  — **High**
**Where:** [`src/poc_server.py`](../src/poc_server.py) (no auth middleware; `/mcp` +
`/health`), tools `get_slide_pptx`, `get_deck`, `get_deck_assets`, `get_deck_outline`,
`get_slide`. Deploy docs ([`docs/POC.md`](POC.md), [`render.yaml`](../render.yaml)) set
**Auth: None**.
**Issue:** The connector is unauthenticated and world-reachable. Anyone with the URL can
enumerate `list_decks()` and then pull **every slide's raw `.pptx` bytes** via
`get_slide_pptx` and every logo via `get_deck_assets` — i.e. reconstruct the firm's
actual client decks verbatim. It is also open compute (unmetered tool calls).
**Impact:** Confidentiality breach of client material; resource/cost abuse. This is the
single biggest risk and is amplified by the `get_slide_pptx` raw-export work — the
corpus is no longer just abstract tags, it's the source decks.
**Note:** This was a *conscious PoC choice* ("read-only, low-sensitivity demo data"),
accepted while the corpus was non-sensitive tags. With raw client decks now served it
should be revisited before any real/sensitive deck ships.
**Remediation:**
- Add an auth gate before serving sensitive data: a bearer-token / shared-secret header
  check in a Starlette middleware (cheap), or platform auth (Cloud Run IAM, an API
  gateway). Make `get_slide_pptx`/`get_deck_assets` require it even if tag-only tools stay open.
- Restrict network exposure: keep behind the deploy platform's auth, drop public tunnels
  for anything real, and **take the service down when not actively demoing**.
- Tier the corpus: only serve `confidentiality_tier == Public` decks publicly (the gate
  in `poc_corpus.load()` could also drop non-public tiers as defense in depth — today it
  never consults `confidentiality_tier`).

### H-2 · Untrusted-document processing chain (tagging side)  — **High (producer)**
**Where:** `slide_tagger` ingests arbitrary `.pptx`/`.pdf` and drives:
LibreOffice ([`extractors/render/soffice.py`](../../slide_tagging/src/slide_tagger/extractors/render/soffice.py)),
poppler/`pdf2image`, `python-pptx`/`lxml`, and Pillow
([`Image.open` in cli.py:299/923, design_system.py:140](../../slide_tagging/src/slide_tagger/extractors/structural/design_system.py)).
**Issue:** Each of these parses attacker-controllable file formats:
- **LibreOffice** has a history of RCE via crafted documents; `pptx_to_pdf` runs it
  headless on the input deck (args are passed as a list with a timeout — *no shell
  injection*, good — but the binary itself is the risk).
- **Pillow `Image.open`** on images extracted from a deck is a **decompression-bomb**
  surface; no `Image.MAX_IMAGE_PIXELS` cap is set, so a crafted image can exhaust memory.
- **`python-pptx`/`lxml`** parse the deck's zipped XML — XML-entity expansion / zip-bomb
  surface on hostile input.
**Impact:** Processing a malicious deck could crash the tagging host (DoS) or, via a
LibreOffice/poppler CVE, achieve code execution on the operator/CI machine.
**Remediation:**
- Treat every input deck as untrusted. Run the tagging pipeline in a **sandbox**
  (container with no network, read-only FS, CPU/mem limits, dropped capabilities), and
  keep LibreOffice + poppler + Pillow + lxml patched.
- Set `Image.MAX_IMAGE_PIXELS` to a sane cap and wrap `Image.open` in a size/`DecompressionBombError` guard.
- Enforce per-file size limits and the existing subprocess timeouts everywhere.

### M-1 · Path traversal in `get_deck_assets` via corpus JSON  — **Medium**
**Where:** [`src/poc_corpus.py` `get_deck_assets`](../src/poc_corpus.py) — `rel =
ip[len("assets/"):] if ip.startswith("assets/") else ip; fpath = self.assets_path /
rel; ... base64.b64encode(fpath.read_bytes())`.
**Issue:** `image_path` comes from the tagged JSON and is **not confined** to
`assets_path`. An absolute path (`/etc/passwd`) or a `../../…` value makes
`self.assets_path / rel` escape the assets dir, and the file is read and base64-returned
to the caller. (`get_slide_pptx` is **not** affected — it normalizes via `Path(sf).…name`,
which strips directory components.)
**Impact:** Arbitrary file read → disclosure, *if* an attacker can plant/modify a tagged
JSON in the corpus. On the read-only public server the corpus isn't writable at runtime,
so this is a **supply-chain / defense-in-depth** issue (a poisoned corpus file, a
compromised producer), not a direct remote exploit — hence Medium.
**Remediation:** Confine the resolved path: reject absolute paths and `..`, then verify
`fpath.resolve().is_relative_to(self.assets_path.resolve())` before reading; also apply
`.name`-style normalization. Validate `image_path` at corpus-load time.

### M-2 · Container runs as root; no resource limits / rate limiting  — **Medium**
**Where:** [`Dockerfile`](../Dockerfile) (no `USER` directive → runs as **root**);
server binds `0.0.0.0`; no rate limiting or concurrency caps anywhere.
**Issue:** The public no-auth endpoint has no throttle, and the process runs as root in
the container, so a parsing bug or memory blow-up (e.g. a very large slide base64) is
both easier to trigger and more damaging if it leads to container compromise.
**Impact:** DoS / cost abuse; larger blast radius on any container escape.
**Remediation:** Add a non-root `USER` to the Dockerfile; set platform CPU/mem limits;
put a rate limit / request-size cap in front (gateway or middleware); pair with H-1's auth.

### M-3 · Confidential client decks egress to Anthropic during enrichment  — **Medium (producer)**
**Where:** [`enrich.py`](../../slide_tagging/src/slide_tagger/enrich.py) (`upload_pdf` +
the Claude API calls) and the `bench`/`enrich` commands.
**Issue:** Enrichment uploads the source deck (PDF) to the Anthropic Files API. This is
intended, but it means **real client decks leave the boundary to a third party**;
whether that's permitted depends on the client's data-handling terms.
**Impact:** Potential contractual / data-residency violation if restricted decks are run
through enrichment.
**Remediation:** Gate enrichment on `confidentiality_tier` (refuse to upload non-public
decks without an explicit override); document the egress; confirm Anthropic data-retention
terms meet the client agreement.

### L-1 · `/health` discloses whether secrets are configured  — **Low**
**Where:** [`src/server.py` `/health`](../src/server.py) returns
`{"checks": {"postgres": ..., "openai_api_key": <bool>}}`.
**Issue:** Minor information disclosure — an unauthenticated caller learns whether an
OpenAI key and Postgres are configured (not their values).
**Remediation:** Keep `/health` to a bare `{"status": "ok"}` for unauthenticated callers;
move detailed checks behind auth.

### L-2 · Future SQL-injection risk in the (unbuilt) pgvector path  — **Low / latent**
**Where:** [`src/retrieval/db.py`](../src/retrieval/db.py) (only `SELECT 1` today) +
[`migrations/001_initial.sql`](../migrations/001_initial.sql); ingestion + query tools
are deferred.
**Issue:** No injection exists now, but when the production retrieval tools are written
they will take user-controlled filter strings. If built with string interpolation
instead of `asyncpg` bind parameters, that's an injection vector.
**Remediation:** When implementing, use parameterized queries exclusively (`$1, $2 …`);
never f-string user input into SQL. Keep `DATABASE_URL` in secrets, not logs.

### L-3 · Dependency & deploy hygiene  — **Low**
**Where:** both repos' lockfiles; the `git add -f corpus/*` deploy flow.
**Issue:** No automated dependency-CVE scanning; `lxml`/`Pillow`/`python-pptx` are
exactly the kind of native-parsing deps that accrue CVEs. The force-add deploy step
risks committing unintended files.
**Remediation:** See ongoing practices below; scope `git add -f` to the explicit
snapshot paths (already corrected in the deploy docs) and never to `.env`.

---

## What's already done right

- **No `eval`/`exec`/`pickle`/`yaml.load`/`os.system`** anywhere; deserialization is
  `json.loads` + Pydantic validation only.
- **Subprocess is injection-safe:** LibreOffice is invoked with a **list** argv (no
  `shell=True`) and a **timeout** ([`soffice.py`](../../slide_tagging/src/slide_tagger/extractors/render/soffice.py)).
- **No SQL string-building** (the only query is a literal `SELECT 1`).
- **Secrets aren't logged:** [`logging.py`](../src/logging.py) is a plain JSON formatter;
  API-key errors print *"is ANTHROPIC_API_KEY set?"* without echoing the value. `.env`
  is gitignored.
- **`get_slide_pptx` path resolution is traversal-safe** (`Path(sf).with_suffix(".pptx").name`).
- **Read-only, one-directional server:** the MCP server accepts no uploads and never
  writes the corpus, so it has **no RCE surface** from remote input — its risk is
  exfiltration/DoS, not compromise.

---

## Prioritized remediation roadmap

1. **H-1 — Add auth + corpus tiering before any sensitive deck ships** (bearer-token
   middleware or platform auth; serve only `Public` decks publicly). Highest impact,
   moderate effort.
2. **H-2 — Sandbox the tagging pipeline** and set `Image.MAX_IMAGE_PIXELS` + size limits;
   keep LibreOffice/poppler/Pillow/lxml patched.
3. **M-2 — Non-root container `USER` + rate limit / request-size cap + platform resource limits.**
4. **M-1 — Confine `get_deck_assets` reads to `assets_path`** (reject `..`/absolute,
   `is_relative_to` check) and validate `image_path` at load.
5. **M-3 — Gate enrichment egress on `confidentiality_tier`;** document third-party data flow.
6. **L-1/L-2/L-3 — Trim `/health`; mandate parameterized SQL in the pgvector build;
   add dependency scanning.**

## Recommended ongoing practices

- **Dependency CVE scanning** in CI (`pip-audit` / `uv pip audit`); pin and routinely
  bump `lxml`, `Pillow`, `python-pptx`, `starlette`, `anthropic`, `asyncpg`.
- **Secret hygiene:** keep keys in the platform secret store (not committed `.env`);
  rotate `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `DATABASE_URL` periodically; confirm
  `corpus/` force-adds never sweep in a secret.
- **Treat every input deck as hostile** on the producer side; treat the **corpus JSON as
  a trust boundary** on the consumer side (validate paths/values at load).
- Re-run this audit when the pgvector path, auth, or any upload capability is added —
  each materially changes the attack surface.
