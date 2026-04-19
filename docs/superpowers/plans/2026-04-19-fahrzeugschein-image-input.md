# Fahrzeugschein Image Input Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional `vehicle_doc_image_url` field to `/api/analyze` that triggers a Claude-vision extraction pass whose five engine-essential fields get prepended to `vehicle_data` before the existing rubric analysis runs. Fail gracefully when the image is unusable.

**Architecture:** All changes confined to `api/index.py`. A separate extraction function calls Claude's vision API with a URL-typed image block, parses a strict JSON response into an `ExtractedSpecs` model, and returns either the specs or a short error code. The orchestrator in `analyze_engine()` runs extraction first (if a URL is present), prepends a labeled block when any fields extracted, then runs the existing rubric pass unchanged. Response envelope gains `image_used: bool` and `image_error: str | None`; request gains `vehicle_doc_image_url: str | None`.

**Tech Stack:** Python 3.11+, FastAPI 0.115.12, Pydantic 2.11.1, Anthropic SDK 0.52.0 (which supports URL-sourced image content blocks natively).

**Spec:** `docs/superpowers/specs/2026-04-19-fahrzeugschein-image-input-design.md`

**Validation approach:** No test harness (per spec). Pure functions verified with inline Python logic checks via `py -c`. Integration validated by pushing to Vercel and running a curl test matrix against the deployed URL, using the known Fahrzeugschein image at `https://img-pa.auto1.com/img4c/3b/4c3b33d9cb165c77cb4b8f206c8a16ca/pa/max-EW55233_fcbf8f9ff0330502607a6b799e4072b2.jpg`.

---

## Task 1: Add `ExtractedSpecs` model and `EXTRACTION_PROMPT` constant

**Files:**
- Modify: `api/index.py` (insert after the existing `FailureOnset` class, before `compute_reliability_score`)

- [ ] **Step 1: Insert `ExtractedSpecs` model**

Find the end of the `FailureOnset` class in `api/index.py` (right after its closing `)` of the `mileage_km` Field). Immediately after that (before `def compute_reliability_score`), insert:

```python
class ExtractedSpecs(BaseModel):
    engine_variant_code: str | None = Field(default=None, description="Engine variant code printed on the Fahrzeugschein (e.g. 'ACGLCF1', 'CFGC').")
    displacement_ccm: int | None = Field(default=None, ge=0, description="Cylinder displacement in cm³.")
    power_kw: int | None = Field(default=None, ge=0, description="Rated power in kilowatts.")
    first_registration: str | None = Field(default=None, description="Date of first registration in YYYY-MM-DD.")
    emissions_class: str | None = Field(default=None, description="Emissions class (e.g. 'EURO5', 'EURO6').")
```

- [ ] **Step 2: Add `EXTRACTION_PROMPT` constant next to the existing `SYSTEM_PROMPT`**

Find the existing line `SYSTEM_PROMPT = """\` in `api/index.py`. Immediately BEFORE that line, insert:

```python
EXTRACTION_PROMPT = """\
You are extracting technical engine identification fields from a photograph of a German \
Zulassungsbescheinigung Teil I (Fahrzeugschein / vehicle registration document). Your \
ONLY job is to read the printed technical fields and return structured JSON. Do NOT \
interpret, score, or comment.

Extract EXACTLY these fields, all optional (return null if not legible):
- engine_variant_code: the engine variant code (often alphanumeric like "ACGLCF1", \
"CFGC", "OM651DE22LA"). Usually appears in a short alphanumeric code field near the \
model code.
- displacement_ccm: engine displacement in cubic centimeters, integer (e.g. 1968).
- power_kw: rated power in kilowatts, integer (e.g. 126).
- first_registration: date of first registration in ISO format YYYY-MM-DD.
- emissions_class: emissions class string (e.g. "EURO5", "EURO6", "EURO6d").

Do NOT extract or mention: VIN/FIN, holder name, holder address, license plate, \
registration authority, stamps, signatures, colors, vehicle class, body type, wheel \
specs, mass figures, or anything else. Those fields are explicitly out of scope.

If the image is not a Fahrzeugschein, is unreadable, or shows no legible technical \
fields, return all fields as null.

Respond with ONLY this JSON (no markdown, no code fences):
{
  "engine_variant_code": "string or null",
  "displacement_ccm": integer or null,
  "power_kw": integer or null,
  "first_registration": "YYYY-MM-DD or null",
  "emissions_class": "string or null"
}
"""


"""

```

The trailing blank `"""` block in the snippet above is NOT part of the insertion — that's just marking where the snippet ends. Only insert the `EXTRACTION_PROMPT = """\ ... """` block plus one blank line.

- [ ] **Step 3: Syntax-check the module**

```bash
cd "O:/CanonScanner/Claude/CheckMyEngine"
py -m py_compile api/index.py && echo "syntax OK"
```
Expected output: `syntax OK`

- [ ] **Step 4: Commit**

```bash
git add api/index.py
git commit -m "$(cat <<'EOF'
Add ExtractedSpecs model and EXTRACTION_PROMPT constant

Prepares for the Fahrzeugschein image input feature. The model holds
the five engine-essential fields (all optional for partial success).
The prompt instructs Claude to read only technical engine fields from
a vehicle registration photograph and explicitly excludes VIN, holder
data, and other non-engine fields.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Add `extract_specs_from_image` and `_format_extracted_block` functions

**Files:**
- Modify: `api/index.py` (insert after `compute_reliability_score`, before `class EngineReport`)

- [ ] **Step 1: Insert the extraction function and formatter**

Find the `def compute_reliability_score(...)` function in `api/index.py`. Immediately after its final `return max(1, min(10, round(raw)))` line (and the blank line following it), and before `class EngineReport`, insert:

```python
async def extract_specs_from_image(url: str) -> tuple[ExtractedSpecs | None, str | None]:
    """Call Claude vision with the Fahrzeugschein URL and parse the structured response.

    Returns (specs, None) when at least one field was extracted.
    Returns (None, error_code) on any failure or when all fields are null.
    Never raises.
    """
    if not url.startswith("https://"):
        return None, "invalid_url_scheme"

    try:
        api_key = os.environ["ANTHROPIC_API_KEY"]
        client = anthropic.AsyncAnthropic(api_key=api_key)
        message = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            temperature=0,
            system=EXTRACTION_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "url", "url": url},
                        },
                        {
                            "type": "text",
                            "text": "Extract the engine identification fields from this Fahrzeugschein. Return only the JSON.",
                        },
                    ],
                }
            ],
        )
        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]
            raw = raw.rsplit("```", 1)[0].strip()
    except Exception:
        return None, "extraction_call_failed"

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None, "extraction_parse_failed"

    try:
        specs = ExtractedSpecs(**data)
    except Exception:
        return None, "extraction_parse_failed"

    if all(v is None for v in specs.model_dump().values()):
        return None, "unreadable_document"

    return specs, None


def _format_extracted_block(specs: ExtractedSpecs) -> str | None:
    """Format non-null fields as the prepended `[Extracted from vehicle registration]`
    block. Returns None if every field is null."""
    lines: list[str] = []
    if specs.engine_variant_code is not None:
        lines.append(f"engine_variant_code: {specs.engine_variant_code}")
    if specs.displacement_ccm is not None:
        lines.append(f"displacement_ccm: {specs.displacement_ccm}")
    if specs.power_kw is not None:
        lines.append(f"power_kw: {specs.power_kw}")
    if specs.first_registration is not None:
        lines.append(f"first_registration: {specs.first_registration}")
    if specs.emissions_class is not None:
        lines.append(f"emissions_class: {specs.emissions_class}")
    if not lines:
        return None
    return "[Extracted from vehicle registration]\n" + "\n".join(lines)
```

- [ ] **Step 2: Syntax-check the module**

```bash
cd "O:/CanonScanner/Claude/CheckMyEngine"
py -m py_compile api/index.py && echo "syntax OK"
```
Expected output: `syntax OK`

- [ ] **Step 3: Verify formatter logic via inline re-implementation (no pydantic needed)**

```bash
cd "O:/CanonScanner/Claude/CheckMyEngine"
py -c "
# Re-implement the formatter inline (mirror of _format_extracted_block) to verify logic
# without requiring pydantic to be installed locally.
def fmt(code, ccm, kw, date, emissions):
    lines = []
    if code is not None: lines.append(f'engine_variant_code: {code}')
    if ccm is not None: lines.append(f'displacement_ccm: {ccm}')
    if kw is not None: lines.append(f'power_kw: {kw}')
    if date is not None: lines.append(f'first_registration: {date}')
    if emissions is not None: lines.append(f'emissions_class: {emissions}')
    if not lines: return None
    return '[Extracted from vehicle registration]\n' + '\n'.join(lines)

# Full extraction case
full = fmt('ACGLCF1', 1968, 126, '2013-04-15', 'EURO5')
assert full == '[Extracted from vehicle registration]\nengine_variant_code: ACGLCF1\ndisplacement_ccm: 1968\npower_kw: 126\nfirst_registration: 2013-04-15\nemissions_class: EURO5', full

# All-null case
assert fmt(None, None, None, None, None) is None

# Partial case (only engine code)
partial = fmt('ACGLCF1', None, None, None, None)
assert partial == '[Extracted from vehicle registration]\nengine_variant_code: ACGLCF1', partial

# Partial case (everything but engine code)
no_code = fmt(None, 1968, 126, '2013-04-15', 'EURO5')
assert no_code == '[Extracted from vehicle registration]\ndisplacement_ccm: 1968\npower_kw: 126\nfirst_registration: 2013-04-15\nemissions_class: EURO5', no_code

print('formatter logic OK')
"
```
Expected output: `formatter logic OK`

- [ ] **Step 4: Commit**

```bash
git add api/index.py
git commit -m "$(cat <<'EOF'
Add extract_specs_from_image and _format_extracted_block

The extraction function calls Claude vision with a URL-sourced image
block, parses the JSON response into ExtractedSpecs, and returns a
(specs, error_code) tuple — never raises. The formatter produces the
labeled block that gets prepended to vehicle_data, omitting null
fields entirely.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Extend request and response models

**Files:**
- Modify: `api/index.py` (the `AnalyzeRequest` and `AnalyzeResponse` classes)

- [ ] **Step 1: Add `vehicle_doc_image_url` to `AnalyzeRequest`**

Find the `AnalyzeRequest` class. Immediately after the existing `language: str | None = Field(...)` declaration (and before the class closes), add:

```python
    vehicle_doc_image_url: str | None = Field(
        default=None,
        description="Optional https URL to a photograph of the Fahrzeugschein (vehicle registration document). If provided, Claude extracts engine-identification fields from the image and prepends them to vehicle_data before analysis. Failures fall back to text-only analysis.",
        examples=["https://img-pa.auto1.com/img4c/.../max-EW55233_....jpg"],
    )
```

So the `AnalyzeRequest` class looks like:

```python
class AnalyzeRequest(BaseModel):
    vehicle_data: str = Field(
        ...,
        description="Free-text vehicle listing string with specs (make, model, year, mileage, etc.). Can be in any language.",
        min_length=10,
        examples=[
            (
                "Mercedes-Benz E-Klasse E 220 CDI BlueEfficiency\n"
                "Build year: 2013\n"
                "Odometer reading: 106,698 km\n"
                "Fuel type: Diesel\n"
                "Horsepower: 125 kW / 170 HP\n"
                "Cylinder capacity: 2,143 ccm"
            )
        ],
    )
    language: str | None = Field(
        default=None,
        description="Response language: 'en', 'de', etc. If omitted, auto-detects from input language.",
        examples=["en", "de"],
    )
    vehicle_doc_image_url: str | None = Field(
        default=None,
        description="Optional https URL to a photograph of the Fahrzeugschein (vehicle registration document). If provided, Claude extracts engine-identification fields from the image and prepends them to vehicle_data before analysis. Failures fall back to text-only analysis.",
        examples=["https://img-pa.auto1.com/img4c/.../max-EW55233_....jpg"],
    )
```

- [ ] **Step 2: Add `image_used` and `image_error` to `AnalyzeResponse`**

Find the `AnalyzeResponse` class. Replace the entire class body with:

```python
class AnalyzeResponse(BaseModel):
    success: bool
    report: EngineReport | None = None
    image_used: bool = False
    image_error: str | None = None
    error: str | None = None
```

- [ ] **Step 3: Syntax-check**

```bash
cd "O:/CanonScanner/Claude/CheckMyEngine"
py -m py_compile api/index.py && echo "syntax OK"
```
Expected: `syntax OK`

- [ ] **Step 4: Commit**

```bash
git add api/index.py
git commit -m "$(cat <<'EOF'
Extend AnalyzeRequest and AnalyzeResponse for image input

AnalyzeRequest gains an optional vehicle_doc_image_url field (defaults
to None so existing callers are unaffected). AnalyzeResponse gains
image_used (bool, default False) and image_error (str | None, default
None) so callers can see whether the image contributed and why it
didn't when it didn't.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Add rule 6 to `SYSTEM_PROMPT`, rewire `analyze_engine()`, update endpoint

**Files:**
- Modify: `api/index.py` (the `SYSTEM_PROMPT` constant, the `analyze_engine` function, and the `/api/analyze` endpoint handler)

This is the activation task — after this commit, the feature is wired end-to-end.

- [ ] **Step 1: Insert rule 6 into `SYSTEM_PROMPT`**

Find rule 5 in `SYSTEM_PROMPT` — the block that starts `5. **Tuning-flag interpretation.**`. Immediately after rule 5's closing sentence (which ends `... must not be treated as wear/warranty risk.`) and before the `## Rubric` line, insert an empty line and then:

```
6. **Registration-document priority.** If the input begins with a \
`[Extracted from vehicle registration]` block, treat those fields as \
authoritative for engine identification. If they conflict with the \
listing text (e.g. listing says "177 HP" but registration says \
"126 kW" which is 171 HP), prefer the registration values. Use the \
`engine_variant_code` as the primary engine identifier.
```

Resulting structure of that portion of the prompt:

```
5. **Tuning-flag interpretation.** A `Tuning: Yes` field or similar indicator \
...
(AHK), auxiliary heater (Standheizung), audio system, or lighting upgrades are \
NOT engine tuning and must not be treated as wear/warranty risk.

6. **Registration-document priority.** If the input begins with a \
`[Extracted from vehicle registration]` block, treat those fields as \
authoritative for engine identification. If they conflict with the \
listing text (e.g. listing says "177 HP" but registration says \
"126 kW" which is 171 HP), prefer the registration values. Use the \
`engine_variant_code` as the primary engine identifier.

## Rubric (sub-scores, 1-10 each)
...
```

- [ ] **Step 2: Rewrite `analyze_engine()` to accept the image URL and return the tuple**

Find the existing `async def analyze_engine(...)` function. Replace the entire function (from `async def` through its final `return` statement) with:

```python
async def analyze_engine(
    vehicle_data: str,
    language: str | None = None,
    vehicle_doc_image_url: str | None = None,
) -> tuple[EngineReport, bool, str | None]:
    """Send vehicle data (optionally enriched by a Fahrzeugschein image) to Claude,
    parse the structured rubric output, and compute the final reliability_score.

    Returns (report, image_used, image_error).
    """
    image_used = False
    image_error: str | None = None
    merged_vehicle_data = vehicle_data

    if vehicle_doc_image_url is not None:
        specs, error = await extract_specs_from_image(vehicle_doc_image_url)
        if specs is not None:
            block = _format_extracted_block(specs)
            if block is not None:
                merged_vehicle_data = f"{block}\n\n[Listing text]\n{vehicle_data}"
                image_used = True
            else:
                image_error = "unreadable_document"
        else:
            image_error = error

    api_key = os.environ["ANTHROPIC_API_KEY"]
    client = anthropic.AsyncAnthropic(api_key=api_key)
    if language:
        lang_instruction = LANGUAGE_INSTRUCTIONS.get(language, f"Write the summary in {language}.")
    else:
        lang_instruction = AUTO_DETECT_INSTRUCTION

    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        temperature=0,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    "Analyze the engine in this vehicle listing and produce "
                    f"the reliability report as JSON. {lang_instruction}\n\n"
                    f"{merged_vehicle_data}"
                ),
            }
        ],
    )

    raw = message.content[0].text.strip()

    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0].strip()

    data = json.loads(raw)

    sub_scores = SubScores(**data["sub_scores"])
    failure_onset = FailureOnset(**data["typical_failure_onset"])
    reliability_score = compute_reliability_score(sub_scores)

    report = EngineReport(
        engine_code=data["engine_code"],
        reliability_score=reliability_score,
        sub_scores=sub_scores,
        typical_failure_onset=failure_onset,
        summary=data["summary"],
    )

    return report, image_used, image_error
```

- [ ] **Step 3: Update the `/api/analyze` endpoint to pass the URL and unpack the tuple**

Find the existing `@app.post("/api/analyze", ...)` handler. Replace its entire body (from the function definition through the final `raise HTTPException(...)`) with:

```python
@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze(request: AnalyzeRequest, _key: str = Security(verify_api_key)):
    """Analyze a vehicle's engine reliability based on listing data and an optional
    Fahrzeugschein image URL."""
    try:
        report, image_used, image_error = await analyze_engine(
            request.vehicle_data,
            request.language,
            request.vehicle_doc_image_url,
        )
        return AnalyzeResponse(
            success=True,
            report=report,
            image_used=image_used,
            image_error=image_error,
        )
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=502, detail=f"Failed to parse engine analysis: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

- [ ] **Step 4: Syntax-check**

```bash
cd "O:/CanonScanner/Claude/CheckMyEngine"
py -m py_compile api/index.py && echo "syntax OK"
```
Expected: `syntax OK`

- [ ] **Step 5: Commit**

```bash
git add api/index.py
git commit -m "$(cat <<'EOF'
Wire image input through analyze_engine and the /api/analyze endpoint

Adds rule 6 to SYSTEM_PROMPT telling the analyzer to prefer extracted
registration fields over listing text. analyze_engine() now accepts an
optional image URL, runs the extraction pass if present, prepends the
formatted block when at least one field is extracted, and returns a
(report, image_used, image_error) tuple. The endpoint unpacks the
tuple and populates the response envelope's new fields.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Push to Vercel and run the validation matrix

**Files:**
- Read only. No code changes.

- [ ] **Step 1: Push all commits**

```bash
cd "O:/CanonScanner/Claude/CheckMyEngine"
git log --oneline -6
git push origin master
```

Vercel auto-deploys. Wait for deployment to finish (typically 30–60s). Confirm with:

```bash
curl -sS https://check-my-engine.vercel.app/api/health
```
Expected: `{"status":"ok"}`.

- [ ] **Step 2: Validation A — Audi A4 with the known Fahrzeugschein URL (happy path)**

```bash
cd "O:/CanonScanner/Claude/CheckMyEngine"
cat > .tmp_payload.json <<'JSON'
{
  "vehicle_data": "Audi A4 2.0 TDI Ambiente\nBuild year: 2013\nFirst registration: 04/2013\nOdometer reading: 86,933 km\nFuel type: Diesel\nHorsepower: 130 kW / 177 HP\nCylinder capacity: 1,968 ccm\nGear box: Manual\nBody type: Combi\nTotal number of owners: 2\nPrior damage / Accident: No\nCountry of origin: DE\nEnvironmental class: EURO 5",
  "vehicle_doc_image_url": "https://img-pa.auto1.com/img4c/3b/4c3b33d9cb165c77cb4b8f206c8a16ca/pa/max-EW55233_fcbf8f9ff0330502607a6b799e4072b2.jpg"
}
JSON
curl -sS -X POST https://check-my-engine.vercel.app/api/analyze \
  -H "X-API-Key: cme_3852ece855d045a4efcc2e4e5b871a79c60bcc86780b293d" \
  -H "Content-Type: application/json" \
  --data-binary @.tmp_payload.json | py -m json.tool
```

Expected:
- `success: true`
- `image_used: true`
- `image_error: null`
- `report.engine_code` contains `ACGLCF1` (the variant code printed on the document)
- `report.sub_scores` and `report.typical_failure_onset` present as usual
- No VIN, no holder name, no license plate anywhere in the summary

Record the response for comparison with Validation B.

- [ ] **Step 3: Validation B — Same car, no image URL (baseline)**

```bash
cd "O:/CanonScanner/Claude/CheckMyEngine"
cat > .tmp_payload.json <<'JSON'
{
  "vehicle_data": "Audi A4 2.0 TDI Ambiente\nBuild year: 2013\nFirst registration: 04/2013\nOdometer reading: 86,933 km\nFuel type: Diesel\nHorsepower: 130 kW / 177 HP\nCylinder capacity: 1,968 ccm\nGear box: Manual\nBody type: Combi\nTotal number of owners: 2\nPrior damage / Accident: No\nCountry of origin: DE\nEnvironmental class: EURO 5"
}
JSON
curl -sS -X POST https://check-my-engine.vercel.app/api/analyze \
  -H "X-API-Key: cme_3852ece855d045a4efcc2e4e5b871a79c60bcc86780b293d" \
  -H "Content-Type: application/json" \
  --data-binary @.tmp_payload.json | py -m json.tool
```

Expected:
- `success: true`
- `image_used: false`
- `image_error: null`
- Response otherwise matches pre-feature behavior (engine_code likely `CAHA/CAHB` or a similar fuzzy identifier — compare with Validation A to confirm the image path made the engine_code more precise).

- [ ] **Step 4: Validation C — Bad URL (graceful fallback)**

```bash
cd "O:/CanonScanner/Claude/CheckMyEngine"
cat > .tmp_payload.json <<'JSON'
{
  "vehicle_data": "Audi A4 2.0 TDI Ambiente\nBuild year: 2013\nFirst registration: 04/2013\nOdometer reading: 86,933 km\nFuel type: Diesel\nHorsepower: 130 kW / 177 HP\nCylinder capacity: 1,968 ccm\nBody type: Combi\nTotal number of owners: 2\nEnvironmental class: EURO 5",
  "vehicle_doc_image_url": "https://example.com/nonexistent.jpg"
}
JSON
curl -sS -X POST https://check-my-engine.vercel.app/api/analyze \
  -H "X-API-Key: cme_3852ece855d045a4efcc2e4e5b871a79c60bcc86780b293d" \
  -H "Content-Type: application/json" \
  --data-binary @.tmp_payload.json | py -m json.tool
```

Expected:
- `success: true`
- `image_used: false`
- `image_error` is one of: `"extraction_call_failed"`, `"extraction_parse_failed"`, or `"unreadable_document"` (any of these is acceptable — exact code depends on how Anthropic's fetcher surfaces the failure)
- `report` is populated (text-only analysis ran successfully)

- [ ] **Step 5: Validation D — Invalid URL scheme**

```bash
cd "O:/CanonScanner/Claude/CheckMyEngine"
cat > .tmp_payload.json <<'JSON'
{
  "vehicle_data": "Audi A4 2.0 TDI Ambiente\nBuild year: 2013\nFirst registration: 04/2013\nOdometer reading: 86,933 km\nFuel type: Diesel\nHorsepower: 130 kW / 177 HP\nCylinder capacity: 1,968 ccm\nBody type: Combi\nTotal number of owners: 2\nEnvironmental class: EURO 5",
  "vehicle_doc_image_url": "http://example.com/insecure.jpg"
}
JSON
curl -sS -X POST https://check-my-engine.vercel.app/api/analyze \
  -H "X-API-Key: cme_3852ece855d045a4efcc2e4e5b871a79c60bcc86780b293d" \
  -H "Content-Type: application/json" \
  --data-binary @.tmp_payload.json | py -m json.tool
```

Expected:
- `success: true`
- `image_used: false`
- `image_error: "invalid_url_scheme"`
- `report` is populated (text-only analysis ran successfully)

- [ ] **Step 6: Success-criteria checklist**

Against the spec:

- [ ] Validation A's `report.engine_code` contains `ACGLCF1` (or the engine variant code as printed on the document)
- [ ] Validation B's response has `image_used: false` and `image_error: null`
- [ ] Validation C's response has `success: true`, `image_used: false`, a populated `report`, and a non-null `image_error`
- [ ] Validation D's `image_error` is exactly `"invalid_url_scheme"`
- [ ] None of the four responses contain VIN, holder name, or address anywhere
- [ ] Validation A's `engine_code` is more precise than Validation B's (narrower variant identification)

If any criterion fails, diagnose before proceeding. Common failure modes:

- `image_used: true` but `engine_code` doesn't contain the variant code → the analyzer prompt's rule 6 isn't taking effect; verify rule 6 was inserted correctly in Task 4, Step 1
- Validation A returns 500 → the vision call may have failed on Vercel (check Anthropic SDK version supports URL image sources; the requirements.txt pins `anthropic==0.52.0` which does support it). Review Vercel function logs.
- `image_error` is always `"extraction_parse_failed"` → the extraction prompt is producing non-JSON; tighten its last line

- [ ] **Step 7: Clean up the temp payload file**

```bash
cd "O:/CanonScanner/Claude/CheckMyEngine"
rm .tmp_payload.json
```

---

## Task 6: Update `API_DOCS.md`

**Files:**
- Modify: `API_DOCS.md`

- [ ] **Step 1: Add the new request field to the Request Body table**

Find the `#### Request Body` section and its table. Replace the table with:

```markdown
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `vehicle_data` | string | Yes | Vehicle listing text (min 10 characters). Can be in any language. |
| `language` | string | No | Response language: `"en"` for English, `"de"` for German. If omitted, auto-detects from input language. |
| `vehicle_doc_image_url` | string | No | `https://` URL to a photograph of the Fahrzeugschein (vehicle registration document). If provided, engine-identification fields are extracted from the image and prepended to `vehicle_data` before analysis. Failures fall back to text-only analysis. |
```

- [ ] **Step 2: Add a new example request that uses the image URL**

Find the section `#### Example Request (Force English response from German input)`. Immediately after that example's closing triple backticks and before the `#### Success Response (200)` heading, insert:

````markdown
#### Example Request (with Fahrzeugschein image URL)

```bash
curl -X POST https://check-my-engine.vercel.app/api/analyze \
  -H "X-API-Key: your-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{
    "vehicle_data": "Audi A4 2.0 TDI Ambiente\nBuild year: 2013\nOdometer reading: 86,933 km\nFuel type: Diesel\nHorsepower: 130 kW / 177 HP",
    "vehicle_doc_image_url": "https://example-listing-host.com/registration-photo.jpg"
  }'
```

The URL must be publicly reachable over `https://`. Claude's vision API fetches the image directly; the CheckMyEngine server never touches the bytes. When the image is used, the response's `image_used` is `true` and the engine code becomes more precise (the exact variant code from the document rather than a text-inferred match).
````

- [ ] **Step 3: Update the Success Response example and Response Fields table**

Find the `#### Success Response (200)` section. Replace the JSON example with:

```json
{
  "success": true,
  "report": {
    "engine_code": "ACGLCF1",
    "reliability_score": 6,
    "sub_scores": {
      "design": 4,
      "mileage": 8,
      "usage": 7,
      "age": 5
    },
    "typical_failure_onset": {
      "years": 8,
      "mileage_km": 150000
    },
    "summary": "Der EA189-Dieselmotor (ACGLCF1) ist durch den Dieselgate-Rückruf und bekannte Probleme mit Steuerkette und AGR-Ventilen belastet. Mit 86.933 km deutlich vor dem typischen Problemfenster. Zwei Vorbesitzer und 12 Jahre Alter sind moderate Risikofaktoren."
  },
  "image_used": true,
  "image_error": null,
  "error": null
}
```

Then replace the Response Fields table with:

```markdown
| Field | Type | Description |
|-------|------|-------------|
| `success` | boolean | Whether the analysis succeeded |
| `report.engine_code` | string | Identified engine code (e.g. "OM651", "CFGC", "ACGLCF1") |
| `report.reliability_score` | integer | Final 1–10 rating, computed as a weighted average of sub-scores |
| `report.sub_scores.design` | integer | 1–10. Intrinsic engine-family quality |
| `report.sub_scores.mileage` | integer | 1–10. Position within the typical failure window |
| `report.sub_scores.usage` | integer | 1–10. Ownership, commercial-use, and accident history |
| `report.sub_scores.age` | integer | 1–10. Age since first registration |
| `report.typical_failure_onset.years` | integer | Typical age in years when issues start for this engine |
| `report.typical_failure_onset.mileage_km` | integer | Typical odometer reading when issues start |
| `report.summary` | string | 2-3 sentence verdict with cited sources |
| `image_used` | boolean | Whether a Fahrzeugschein image contributed to this analysis |
| `image_error` | string or null | Short error code if `vehicle_doc_image_url` was provided but unusable; `null` otherwise. Possible values: `"invalid_url_scheme"`, `"extraction_call_failed"`, `"extraction_parse_failed"`, `"unreadable_document"` |
| `error` | string or null | Error message if the analysis failed |
```

- [ ] **Step 4: Commit**

```bash
cd "O:/CanonScanner/Claude/CheckMyEngine"
git add API_DOCS.md
git commit -m "$(cat <<'EOF'
Document vehicle_doc_image_url and image_used/image_error fields

Adds the optional request field and documents the graceful fallback
behavior when image extraction fails. Includes a curl example using
an image URL and lists the possible image_error codes.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git push origin master
```

---

## Task 7: Update `PROGRESS.md`

**Files:**
- Modify: `PROGRESS.md`

- [ ] **Step 1: Add the completed entry**

Find the line `- [x] Structured scoring rubric (design/mileage/usage/age sub-scores + failure-window)` in `PROGRESS.md`. Immediately after it, add:

```markdown
- [x] Fahrzeugschein image input (optional vehicle_doc_image_url for precise engine identification)
```

- [ ] **Step 2: Commit and push**

```bash
cd "O:/CanonScanner/Claude/CheckMyEngine"
git add PROGRESS.md
git commit -m "$(cat <<'EOF'
Mark Fahrzeugschein image input as complete in PROGRESS.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git push origin master
```
