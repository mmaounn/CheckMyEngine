# Fahrzeugschein Image Input — Design Spec

**Date:** 2026-04-19
**Status:** Approved, ready for implementation planning
**Author:** CheckMyEngine team

## Problem

The structured scoring rubric shipped on 2026-04-19 makes the rubric's factors explainable but still depends on the model correctly identifying the engine from the text listing. Text listings like "Audi A4 2.0 TDI 177 HP, 2013" match several possible EA189 variants (CAHA, CAHB, CGLB, CGLC), and the model's chosen code drifts between runs. The `design` sub-score — the strongest driver of the final reliability score — is therefore only as deterministic as the engine identification, and engine identification is the weakest deterministic link.

Germany's *Zulassungsbescheinigung Teil I* ("Fahrzeugschein") prints the exact engine variant code (e.g. `ACGLCF1`) alongside displacement, power, first-registration date, and emissions class. Most of the target user's workflow (AUTO1 auction listings) publishes a scanned Fahrzeugschein photo at a stable public CDN URL. Feeding that photo into the analysis pipeline eliminates the engine-identification guesswork at the source.

## Goals

- Accept an optional image URL in the `/api/analyze` request and use it to extract engine-identification facts (engine variant code, displacement, power, first-registration date, emissions class) via Claude's vision API.
- Merge the extracted facts into the existing analysis pipeline without changing the rubric prompt's structure or response shape (beyond two additive fields).
- Keep the feature backward-compatible: callers that don't send an image see exactly the behavior from 2026-04-19's rubric release.
- Degrade gracefully when image input is present but unusable (404, unreadable, wrong document, fetch error). Never fail the whole request because the image failed.

## Non-Goals

- Base64 / multipart image upload. URL-only for v1.
- VIN extraction or any non-engine field extraction (holder, address, license plate, full factory code string beyond what's needed for engine ID).
- Caching of extraction results by image-URL hash.
- Domain allowlist enforcement (beyond requiring `https://` scheme). Anthropic's vision fetcher enforces its own protections against private-address fetching.
- Image size, dimension, or format validation. Anthropic enforces its own limits; we return whatever error Anthropic returns.
- Support for Fahrzeugbrief (Zulassungsbescheinigung Teil II), COC papers, or non-German registration documents. The extraction prompt is tuned for Fahrzeugschein; other documents will likely fail extraction and fall back to text-only analysis — which is fine.
- Caching for identical-input non-determinism (tracked separately).

## Design

### Request shape

The existing `AnalyzeRequest` gains one optional field:

```json
{
  "vehicle_data": "Audi A4 2.0 TDI Ambiente\nBuild year: 2013\n...",
  "language": "de",
  "vehicle_doc_image_url": "https://img-pa.auto1.com/img4c/..../max-EW55233_....jpg"
}
```

- `vehicle_data` — unchanged. Still required.
- `language` — unchanged.
- `vehicle_doc_image_url` — new, optional. Must start with `https://`. Passed through to Claude's vision API as a `{"type": "image", "source": {"type": "url", "url": "..."}}` content block. No server-side fetching.

### Response shape

The existing `AnalyzeResponse` gains two fields:

```json
{
  "success": true,
  "report": { /* unchanged rubric shape */ },
  "image_used": true,
  "image_error": null,
  "error": null
}
```

- `image_used` — `true` if at least one field was successfully extracted from the image and prepended to the analysis input. `false` otherwise (including when no image URL was provided).
- `image_error` — `null` on success or when no image was provided; short machine-readable string on failure (e.g. `"fetch_failed"`, `"unreadable_document"`, `"extraction_call_failed"`). Never contains user data or stack traces.

The existing `report` shape (engine_code, reliability_score, sub_scores, typical_failure_onset, summary) is unchanged whether the image was used or not.

### Two-pass flow

The analysis pipeline becomes:

1. **If `vehicle_doc_image_url` is present:**
   - Call Claude (`claude-sonnet-4-6`, `temperature=0`, `max_tokens=300`) with a dedicated extraction prompt (see below) and a single user message containing a `type: image` block referencing the URL.
   - Parse the JSON response into `ExtractedSpecs` (all fields optional).
   - If any field was successfully extracted, format as a `[Extracted from vehicle registration]` block (see below) and prepend to `vehicle_data`. Set `image_used = true`.
   - If all fields are null or extraction failed, skip prepending. Set `image_used = false` and `image_error` accordingly.
2. **Always run the existing analysis pass** on the (possibly prepended) `vehicle_data`.

Both passes are `await`'d sequentially; total latency is roughly doubled when image is present (still sub-5s typical).

### Extraction prompt

```
You are extracting technical engine identification fields from a photograph of a German
Zulassungsbescheinigung Teil I (Fahrzeugschein / vehicle registration document). Your ONLY
job is to read the printed technical fields and return structured JSON. Do NOT interpret,
score, or comment.

Extract EXACTLY these fields, all optional (return null if not legible):
- engine_variant_code: the engine variant code (often alphanumeric like "ACGLCF1", "CFGC",
  "OM651DE22LA"). Usually appears in a short alphanumeric code field near the model code.
- displacement_ccm: engine displacement in cubic centimeters, integer (e.g. 1968).
- power_kw: rated power in kilowatts, integer (e.g. 126).
- first_registration: date of first registration in ISO format YYYY-MM-DD.
- emissions_class: emissions class string (e.g. "EURO5", "EURO6", "EURO6d").

Do NOT extract or mention: VIN/FIN, holder name, holder address, license plate,
registration authority, stamps, signatures, colors, vehicle class, body type, wheel specs,
mass figures, or anything else. Those fields are explicitly out of scope.

If the image is not a Fahrzeugschein, is unreadable, or shows no legible technical fields,
return all fields as null.

Respond with ONLY this JSON (no markdown, no code fences):
{
  "engine_variant_code": "string or null",
  "displacement_ccm": integer or null,
  "power_kw": integer or null,
  "first_registration": "YYYY-MM-DD or null",
  "emissions_class": "string or null"
}
```

### Prepended block format

When extraction succeeds (fully or partially), the analyzer receives the original `vehicle_data` with this block prepended:

```
[Extracted from vehicle registration]
engine_variant_code: ACGLCF1
displacement_ccm: 1968
power_kw: 126
first_registration: 2013-04-15
emissions_class: EURO5

[Listing text]
Audi A4 2.0 TDI Ambiente
Build year: 2013
...
```

Null fields are omitted from the block entirely (no `engine_variant_code: null` lines). If all fields are null, the block is not added.

### Analyzer prompt delta

The existing `SYSTEM_PROMPT` gains one new rule, inserted after rule 5 (tuning-flag interpretation) and before the Rubric section:

```
6. **Registration-document priority.** If the input begins with a
"[Extracted from vehicle registration]" block, treat those fields as
authoritative for engine identification. If they conflict with the
listing text (e.g. listing says "177 HP" but registration says
"126 kW" which is 171 HP), prefer the registration values. Use the
engine_variant_code as the primary engine identifier.
```

The rest of the prompt — rubric definitions, output schema, anti-fabrication rules — stays exactly as it is.

### Failure modes and `image_error` values

| Situation | `image_used` | `image_error` | Rest of response |
|---|---|---|---|
| No image URL in request | `false` | `null` | Normal text-only analysis |
| URL not `https://` | `false` | `"invalid_url_scheme"` | Text-only analysis proceeds |
| Anthropic vision call fails (includes the URL being unreachable from Anthropic's side, network errors, rate limits, 4xx/5xx fetch results) | `false` | `"extraction_call_failed"` | Text-only analysis proceeds |
| Anthropic fetched image but returned all-null fields | `false` | `"unreadable_document"` | Text-only analysis proceeds |
| Anthropic returned non-JSON or malformed JSON | `false` | `"extraction_parse_failed"` | Text-only analysis proceeds |
| Partial extraction (some fields, not all) | `true` | `null` | Block prepended with only the non-null fields |
| Full extraction | `true` | `null` | Block prepended with all fields |

In all cases `success: true` and the response contains a valid `report`. We never return HTTP 5xx because the image step failed.

### Code changes

Single file: `api/index.py`. Additions:

- New Pydantic model `ExtractedSpecs` with five optional fields.
- New async function `extract_specs_from_image(url: str) -> tuple[ExtractedSpecs | None, str | None]`. Returns `(specs, None)` on success, `(None, error_code)` on failure. Does not raise.
- New helper `_format_extracted_block(specs: ExtractedSpecs) -> str | None`. Returns the formatted block string if at least one field is non-null; `None` otherwise.
- Extract the existing extraction prompt constant: `EXTRACTION_PROMPT` alongside `SYSTEM_PROMPT`.
- `analyze_engine()` gains an optional `vehicle_doc_image_url: str | None = None` parameter. When present, runs extraction, optionally prepends the block, and runs the analysis pass as usual. Returns `(EngineReport, image_used: bool, image_error: str | None)`.
- `AnalyzeRequest` gains `vehicle_doc_image_url: str | None`.
- `AnalyzeResponse` gains `image_used: bool = False` and `image_error: str | None = None`.
- `SYSTEM_PROMPT` gains rule 6 (registration-document priority).
- `/api/analyze` endpoint passes the new field through and populates the new response fields.

No new files. No new dependencies. The existing `anthropic` SDK already supports image content blocks with URL source.

## Validation

Test against the deployed API (same pattern as the rubric release):

- Variant D (Audi A4 2.0 TDI Ambiente, 87k km) with the known image URL from the 2026-04-19 test: `https://img-pa.auto1.com/img4c/3b/4c3b33d9cb165c77cb4b8f206c8a16ca/pa/max-EW55233_fcbf8f9ff0330502607a6b799e4072b2.jpg`. Expect `image_used: true`, `engine_code` containing `ACGLCF1` or equivalent precise variant.
- Variant D *without* the image URL. Expect `image_used: false`, `image_error: null`, `engine_code` as before (`CAHA/CAHB` or similar).
- Variant D with a deliberately bad URL (`https://example.com/nonexistent.jpg`). Expect `image_used: false`, `image_error: "extraction_call_failed"`, plus a valid text-only report.
- Variant D with a non-document image URL (e.g. an exterior car photo from the same AUTO1 listing). Expect `image_used: false`, `image_error: "unreadable_document"`, plus a valid text-only report.
- Variant D with the document URL, run twice. Verify engine_code is consistent across runs (stability check).

Success criteria:
- With image: `engine_code` in the response contains the exact variant code from the document (e.g. `ACGLCF1`).
- Without image: response is identical to the pre-feature behavior.
- Bad URL: valid response, `image_used: false`, text-only analysis matches the no-image case.
- No VIN, holder name, or address appears anywhere in the response even when the image is used.

## Open questions / future work

- **Base64 / multipart upload.** Add `vehicle_doc_image_b64` as an alternative input path if users need to submit local photos not hosted on a public URL.
- **Caching by image URL.** If we later add request caching, `vehicle_doc_image_url` should be part of the cache key so an updated document doesn't return stale analysis.
- **Multi-language document support.** The extraction prompt currently assumes a German Fahrzeugschein. Other countries' registration documents (Swiss Fahrzeugausweis, Austrian Zulassungsschein, French Carte Grise) have similar fields in different layouts — worth adding if volume warrants.
- **Structured extracted-fields echo.** Consider surfacing the `ExtractedSpecs` object itself in the response (e.g. `report.extracted_from_image: {...}`) so callers can see exactly what was read, not just the `image_used` boolean. Out of scope for v1 to keep the response shape minimal.
