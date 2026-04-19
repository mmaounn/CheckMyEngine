# Structured Scoring Rubric Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the monolithic `reliability_score` with four explainable sub-scores the model fills in, then compute the final score deterministically in Python. Add a `typical_failure_onset` field. Tighten the prompt's anti-fabrication and tuning-flag rules.

**Architecture:** All changes are confined to a single file (`api/index.py`). The Claude model emits sub-scores and a failure-window — it no longer emits `reliability_score`. A pure Python function computes the final score from the weighted average of sub-scores. Response envelope and URL surface are unchanged; `report` gains two structured fields.

**Tech Stack:** Python 3.11+, FastAPI 0.115.12, Pydantic 2.11.1, Anthropic SDK 0.52.0.

**Spec:** `docs/superpowers/specs/2026-04-19-structured-scoring-rubric-design.md`

**Validation approach:** The spec scopes out a test harness. Pure functions are verified with inline Python assertions run via `python -c`. Integration is validated by running a curl test matrix against the deployed Vercel URL. No pytest suite is added.

---

## Task 1: Add `SubScores` and `FailureOnset` Pydantic models

**Files:**
- Modify: `api/index.py` (after the existing `EngineReport` class around line 49)

- [ ] **Step 1: Open `api/index.py` and locate the `EngineReport` class (currently at line 45–48).** The two new classes will be inserted just before `EngineReport` so `EngineReport` can reference them.

- [ ] **Step 2: Insert `SubScores` and `FailureOnset` before `EngineReport`**

Replace the `EngineReport` definition (lines 45–48 in the current file) with this block:

```python
class SubScores(BaseModel):
    design: int = Field(
        description="1-10. Intrinsic quality of the engine family (design soundness + severity of known issues), independent of this specific vehicle.",
        ge=1,
        le=10,
    )
    mileage: int = Field(
        description="1-10. How far into this engine's typical failure window the car sits. 10 = <25% of onset, 7 = 50-75%, 5 = at onset, 3 = 100-130%, 1 = >130%.",
        ge=1,
        le=10,
    )
    usage: int = Field(
        description="1-10. Combined history judgment: owner count, commercial/rental/taxi use, accidents, explicit chip tuning.",
        ge=1,
        le=10,
    )
    age: int = Field(
        description="1-10. Age since first registration relative to engine's typical durability. 10 = <3 years, 7 = 5-8, 5 = 10, 3 = 15+, 1 = 20+.",
        ge=1,
        le=10,
    )


class FailureOnset(BaseModel):
    years: int = Field(
        description="Typical age in years at which meaningful issues begin to surface for this engine family.",
        ge=0,
    )
    mileage_km: int = Field(
        description="Typical odometer reading at which meaningful issues begin to surface for this engine family.",
        ge=0,
    )


class EngineReport(BaseModel):
    engine_code: str = Field(description="The identified engine code/designation (e.g. OM651)")
    reliability_score: int = Field(description="Final 1-10 score computed from sub-scores (weighted average).", ge=1, le=10)
    sub_scores: SubScores = Field(description="Breakdown of the rubric factors that produce the final score.")
    typical_failure_onset: FailureOnset = Field(description="Point estimate of when issues typically start for this engine family.")
    summary: str = Field(description="2-3 sentence verdict on engine reputation, reliability, and mileage risk")
```

- [ ] **Step 3: Verify the module still imports cleanly**

Run from the repo root:
```bash
python -c "from api.index import SubScores, FailureOnset, EngineReport; print('models OK')"
```
Expected output: `models OK`

If the command fails with a missing env var, set a dummy value first:
```bash
ANTHROPIC_API_KEY=dummy CME_API_KEYS=dummy python -c "from api.index import SubScores, FailureOnset, EngineReport; print('models OK')"
```

- [ ] **Step 4: Commit**

```bash
git add api/index.py
git commit -m "$(cat <<'EOF'
Add SubScores and FailureOnset models to EngineReport

Prepares for structured rubric scoring: four 1-10 sub-scores (design,
mileage, usage, age) and a typical-failure-onset point estimate. The
reliability_score field is retained but will be computed in Python from
the sub-scores rather than emitted by the model.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Add the pure `compute_reliability_score` function

**Files:**
- Modify: `api/index.py` (insert after the `FailureOnset` class, before `EngineReport`)

- [ ] **Step 1: Insert the scoring function**

Find the `EngineReport` class you added in Task 1. Immediately before it (after `FailureOnset`), insert:

```python
def compute_reliability_score(sub_scores: "SubScores") -> int:
    """Weighted-average of sub-scores, rounded and clamped to [1, 10]."""
    raw = (
        0.4 * sub_scores.design
        + 0.3 * sub_scores.mileage
        + 0.2 * sub_scores.usage
        + 0.1 * sub_scores.age
    )
    return max(1, min(10, round(raw)))
```

The forward reference `"SubScores"` as a string is required because `SubScores` is already defined above, but Pydantic may re-import during class construction — stringifying the annotation sidesteps any edge case.

- [ ] **Step 2: Verify the arithmetic inline**

Run from the repo root:
```bash
ANTHROPIC_API_KEY=dummy CME_API_KEYS=dummy python -c "
from api.index import SubScores, compute_reliability_score

# Spec example: design=7, mileage=8, usage=9, age=8 -> 7.8 -> 8
s1 = SubScores(design=7, mileage=8, usage=9, age=8)
assert compute_reliability_score(s1) == 8, compute_reliability_score(s1)

# Worst case: all 1s -> 1
s2 = SubScores(design=1, mileage=1, usage=1, age=1)
assert compute_reliability_score(s2) == 1, compute_reliability_score(s2)

# Best case: all 10s -> 10
s3 = SubScores(design=10, mileage=10, usage=10, age=10)
assert compute_reliability_score(s3) == 10, compute_reliability_score(s3)

# Run 1 equivalent (155k + rental + accident): design=7, mileage=4, usage=3, age=7 -> 5.2 -> 5
s4 = SubScores(design=7, mileage=4, usage=3, age=7)
assert compute_reliability_score(s4) == 5, compute_reliability_score(s4)

print('scoring OK')
"
```
Expected output: `scoring OK`

If any assertion fails, the printed value is the actual score — fix the formula or the expected values.

- [ ] **Step 3: Commit**

```bash
git add api/index.py
git commit -m "$(cat <<'EOF'
Add compute_reliability_score() for deterministic final scoring

Pure weighted-average function (0.4 design + 0.3 mileage + 0.2 usage +
0.1 age), rounded and clamped to [1, 10]. This replaces the model's
direct emission of reliability_score, giving us reproducible scoring
given fixed sub-scores.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Rewrite `SYSTEM_PROMPT` with rubric definitions and tightened rules

**Files:**
- Modify: `api/index.py` (the `SYSTEM_PROMPT` constant, currently at lines 59–107)

- [ ] **Step 1: Replace `SYSTEM_PROMPT` wholesale**

Delete the current `SYSTEM_PROMPT = """..."""` block (from `SYSTEM_PROMPT = """\` through its closing `"""`) and replace it with:

```python
SYSTEM_PROMPT = """\
You are an automotive engine reliability analyst. Your job is to identify the \
exact engine in a vehicle listing and produce a factual, structured reliability \
report.

## Rules

1. **Identify the engine code** from the vehicle make, model, year, displacement, \
and power output. Be specific (e.g. "OM651 DE 22 LA" not just "diesel engine"). \
The input listing may be in any language (English, German, etc.) — parse it regardless.

2. **Every claim must be traceable.** For each known issue, recall, or rating you \
provide, cite the source:
   - Published reliability studies ("TÜV Report 2023", "ADAC Pannenstatistik 2022", \
"Consumer Reports 2020 Annual Auto Issue")
   - Well-known automotive engineering references
   - Major enthusiast/owner forums ONLY for widely corroborated issues

3. **Do NOT invent specific recall/TSB/NHTSA IDs.** A plausible-looking but \
fabricated ID ("KBA-Rückruf 23/2909", "Mercedes TSB LI07.00-P-054321") is worse \
than no ID. If you are not highly confident a specific ID is real and corresponds \
to this exact engine, write "widely reported by owners", "ADAC Pannenstatistik" \
with a year range, or "covered by Dieselgate recall" instead. Only cite a specific \
recall/TSB ID if you are certain it is real.

4. **Use precise technical terminology.** Never confuse similar-sounding issues. \
For example: Ölverbrauch (oil consumption — engine burns oil internally) is NOT the same as \
Ölverlust (oil leak — external seal/gasket failure). Always use the correct term.

5. **Tuning-flag interpretation.** A `Tuning: Yes` field or similar indicator \
is informational only. Do NOT assume chip tuning or engine remapping unless the \
tuning note explicitly describes it (e.g. "Chiptuning", "Leistungssteigerung", \
"ECU remap", "performance software"). Accessory retrofits such as trailer hitch \
(AHK), auxiliary heater (Standheizung), audio system, or lighting upgrades are \
NOT engine tuning and must not be treated as wear/warranty risk.

## Rubric (sub-scores, 1-10 each)

Score each factor independently:

- **design** — Intrinsic engine-family quality. 10 = legendary (e.g. Toyota 2JZ), \
7 = solid with minor issues, 5 = average with known headaches, 3 = significant \
design flaws, 1 = fundamentally broken. Ignore this specific car's mileage, age, \
or history here.
- **mileage** — How far this specific car's odometer sits into the engine's \
typical failure window. Compute `car_km / typical_failure_onset.mileage_km`. \
10 = <25%, 7 = 50-75%, 5 = at onset (~100%), 3 = 100-130%, 1 = >130%.
- **usage** — Combined history: owner count, commercial/rental/taxi use, \
accidents, explicit chip tuning. 10 = single owner, private, no accidents; \
7 = typical consumer history; 5 = notable concern (multiple owners OR commercial); \
3 = serious concern (e.g. rental + accident); 1 = likely abused.
- **age** — Years since first registration. 10 = <3 years, 7 = 5-8 years, \
5 = ~10 years, 3 = 15+ years, 1 = 20+ years.

## Failure-onset estimate

Also estimate when issues typically start for this engine family as a single \
point (not a range):
- `years` — typical age in years
- `mileage_km` — typical odometer reading

These anchor the `mileage` sub-score; be consistent.

## Summary

2-3 short sentences, under 280 characters total. State the engine family verdict, \
this car's position relative to the failure window, and the overall risk. Do NOT \
list every known issue. Do NOT include a numeric score — the score is computed \
from the sub-scores.

## Output format

Respond with ONLY this JSON (no markdown, no code fences). Do NOT include a \
`reliability_score` field — it is computed server-side.

{
  "engine_code": "string",
  "sub_scores": {
    "design": 1-10,
    "mileage": 1-10,
    "usage": 1-10,
    "age": 1-10
  },
  "typical_failure_onset": {
    "years": integer,
    "mileage_km": integer
  },
  "summary": "2-3 sentences, <280 chars, no numeric score"
}
"""
```

- [ ] **Step 2: Verify the module still imports**

```bash
ANTHROPIC_API_KEY=dummy CME_API_KEYS=dummy python -c "from api.index import SYSTEM_PROMPT; print(len(SYSTEM_PROMPT), 'chars')"
```
Expected: a number around 2500–3500. No traceback.

- [ ] **Step 3: Commit**

```bash
git add api/index.py
git commit -m "$(cat <<'EOF'
Rewrite SYSTEM_PROMPT for structured rubric and tightened rules

- Model now emits sub_scores + typical_failure_onset, not reliability_score
- Inline rubric anchors for each of the four sub-scores
- Stricter anti-fabrication rule: no invented recall/TSB IDs
- Explicit tuning-flag rule: accessories are not engine tuning

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Rewrite `analyze_engine()` to parse the new shape and compute the score

**Files:**
- Modify: `api/index.py` (the `analyze_engine()` function, currently at lines 120–154)

- [ ] **Step 1: Replace `analyze_engine()` wholesale**

Delete the current `async def analyze_engine(...):` function body and replace with:

```python
async def analyze_engine(vehicle_data: str, language: str | None = None) -> EngineReport:
    """Send vehicle data to Claude, parse the structured rubric output, and
    compute the final reliability_score from the sub-scores."""
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
                    f"{vehicle_data}"
                ),
            }
        ],
    )

    raw = message.content[0].text.strip()

    # Handle case where model wraps JSON in code fences despite instructions
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0].strip()

    data = json.loads(raw)

    sub_scores = SubScores(**data["sub_scores"])
    failure_onset = FailureOnset(**data["typical_failure_onset"])
    reliability_score = compute_reliability_score(sub_scores)

    return EngineReport(
        engine_code=data["engine_code"],
        reliability_score=reliability_score,
        sub_scores=sub_scores,
        typical_failure_onset=failure_onset,
        summary=data["summary"],
    )
```

- [ ] **Step 2: Verify the module imports and the function signature is correct**

```bash
ANTHROPIC_API_KEY=dummy CME_API_KEYS=dummy python -c "
from api.index import analyze_engine
import inspect
assert inspect.iscoroutinefunction(analyze_engine)
print('analyze_engine OK')
"
```
Expected: `analyze_engine OK`

- [ ] **Step 3: Commit**

```bash
git add api/index.py
git commit -m "$(cat <<'EOF'
Wire analyze_engine to new rubric: parse sub-scores and compute final score

The function now parses sub_scores and typical_failure_onset from the
model's JSON output and computes reliability_score server-side via
compute_reliability_score(). The response shape gains sub_scores and
typical_failure_onset as structured fields.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Run locally and validate with the test matrix

**Files:**
- Read only. No code changes.

This task validates the implementation against the spec's success criteria before pushing to Vercel.

- [ ] **Step 1: Start the API locally**

From the repo root, in a separate terminal:
```bash
ANTHROPIC_API_KEY="<real-key>" CME_API_KEYS="cme_3852ece855d045a4efcc2e4e5b871a79c60bcc86780b293d" \
  python -m uvicorn api.index:app --reload --port 8000
```

The server listens on `http://127.0.0.1:8000`. Keep it running for the rest of this task.

Note: the real `ANTHROPIC_API_KEY` must be a valid Anthropic key — the dummy value used earlier will cause `analyze_engine` to fail at request time. Copy it from Vercel's env vars or from `.env` if present. Do not commit the key.

- [ ] **Step 2: Test variant A — 2015 VW Sharan, 155k km, rental + accident**

```bash
curl -sS -X POST http://127.0.0.1:8000/api/analyze \
  -H "X-API-Key: cme_3852ece855d045a4efcc2e4e5b871a79c60bcc86780b293d" \
  -H "Content-Type: application/json" \
  -d '{"vehicle_data": "Volkswagen Sharan 2.0 TDI Cup BlueMotion Technology\nBuild year: 2015\nFirst registration: 05/2015\nOdometer reading: 155,327 km\nCommercial use: Rental\nFuel type: Diesel\nHorsepower: 103 kW / 140 HP\nCylinder capacity: 1,968 ccm\nGear box: DSG\nBody type: Van\nTotal number of owners: 2\nPrior damage / Accident: Yes\nCountry of origin: DE\nEnvironmental class: EURO 5\nCO2 Emissions: 149g/km"}' | python -m json.tool
```
Expected: JSON response with `success: true`, `report.sub_scores` containing `design/mileage/usage/age`, `report.typical_failure_onset` with `years` and `mileage_km`, and `report.reliability_score` matching `round(0.4*design + 0.3*mileage + 0.2*usage + 0.1*age)`. Note the sub-scores — especially `design` and `typical_failure_onset.mileage_km`.

- [ ] **Step 3: Test variant B — 2015 VW Sharan, 55k km, rental + accident**

Same curl as above but change `Odometer reading: 155,327 km` to `Odometer reading: 55,327 km`.

Expected: `design` sub-score should match variant A (engine family is the same). `typical_failure_onset.mileage_km` should match variant A. `mileage` sub-score should be higher (lower km → further from onset). `usage` sub-score should match A (same history flags).

- [ ] **Step 4: Test variant C — 2015 VW Sharan, 55k km, clean history**

```bash
curl -sS -X POST http://127.0.0.1:8000/api/analyze \
  -H "X-API-Key: cme_3852ece855d045a4efcc2e4e5b871a79c60bcc86780b293d" \
  -H "Content-Type: application/json" \
  -d '{"vehicle_data": "Volkswagen Sharan 2.0 TDI Cup BlueMotion Tech\nBuild year: 2015\nFirst registration: 05/2015\nOdometer reading: 55327 km\nFuel type: Diesel\nHorsepower: 103 kW / 140 HP\nTuning note: Abnehmbare AHK nachgeruestet, Standheizung nachgeruestet.\nCylinder capacity: 1968 ccm\nGear box: Duplex\nBody type: Van\nTotal number of owners: 2\nCountry of origin: DE\nEnvironmental class: EURO 5\nCO2 Emissions: 149g/km"}' | python -m json.tool
```
Expected: `usage` sub-score clearly higher than variants A and B (no rental, no accident). Summary must NOT mention "Chiptuning", "Chiptuning-Verdacht", or "Leistungssteigerung" — the tuning note only describes accessory retrofits.

- [ ] **Step 5: Test variant D — 2013 Audi A4 2.0 TDI (cross-engine check)**

```bash
curl -sS -X POST http://127.0.0.1:8000/api/analyze \
  -H "X-API-Key: cme_3852ece855d045a4efcc2e4e5b871a79c60bcc86780b293d" \
  -H "Content-Type: application/json" \
  -d '{"vehicle_data": "Audi A4 2.0 TDI Ambiente\nBuild year: 2013\nFirst registration: 04/2013\nOdometer reading: 86,933 km\nFuel type: Diesel\nHorsepower: 130 kW / 177 HP\nCylinder capacity: 1,968 ccm\nGear box: Manual\nBody type: Combi\nTotal number of owners: 2\nPrior damage / Accident: No\nCountry of origin: DE\nEnvironmental class: EURO 5"}' | python -m json.tool
```
Expected: different engine code (CGLC or similar), plausible sub-scores. Summary must NOT cite a specific KBA/TSB ID unless verifiable — the prior API response fabricated "KBA-Rückruf 2015, Az. 3673". Phrases like "widely reported", "ADAC Pannenstatistik", or "covered by Dieselgate recall" (without a number) are acceptable.

- [ ] **Step 6: Determinism spot-check — run variant C twice**

Repeat the curl from Step 4. Compare the two responses:
- `engine_code` should be identical
- `sub_scores` may vary slightly (this is the known non-determinism we didn't fix)
- If all four sub-scores are identical, `reliability_score` is also identical by construction

Note any large drift (>1 in any sub-score) — if the rubric anchors are tight enough, drift should be smaller than the pre-rubric version.

- [ ] **Step 7: Success-criteria checklist**

Against the spec:
- [ ] Same-engine-family variants (A, B, C) produce the same `design` sub-score
- [ ] `mileage` sub-score increases monotonically as km decreases (A < B ≈ C)
- [ ] `usage` sub-score is lower in A and B than in C
- [ ] No fabricated recall IDs appear in any of the four summaries (A–D)
- [ ] Variant C does not produce "Chiptuning" language

If any criterion fails, diagnose:
- `design` varies across A/B/C → prompt rubric anchor for design is ambiguous; tighten the anchor's phrasing
- `mileage` doesn't scale with km → model is not using `typical_failure_onset.mileage_km` as the denominator; reinforce the formula in the prompt
- Fabricated ID appears → rule 3 in the prompt needs a stronger example of a rejected ID

Fix, re-run the failing variants, then proceed.

- [ ] **Step 8: Stop the local server**

Ctrl-C the uvicorn process.

- [ ] **Step 9: Push to deploy**

```bash
git log --oneline -5
git push origin master
```

Vercel will auto-deploy. Wait ~60 seconds, then smoke-test the deployment:
```bash
curl -sS https://check-my-engine.vercel.app/api/health
```
Expected: `{"status":"ok"}`.

Re-run variant C against the deployed URL to confirm the change is live:
```bash
curl -sS -X POST https://check-my-engine.vercel.app/api/analyze \
  -H "X-API-Key: cme_3852ece855d045a4efcc2e4e5b871a79c60bcc86780b293d" \
  -H "Content-Type: application/json" \
  -d '{"vehicle_data": "Volkswagen Sharan 2.0 TDI Cup BlueMotion Tech\nBuild year: 2015\nFirst registration: 05/2015\nOdometer reading: 55327 km\nFuel type: Diesel\nHorsepower: 103 kW / 140 HP\nTuning note: Abnehmbare AHK nachgeruestet, Standheizung nachgeruestet.\nCylinder capacity: 1968 ccm\nBody type: Van\nTotal number of owners: 2\nEnvironmental class: EURO 5"}' | python -m json.tool
```
Expected: response includes `sub_scores` and `typical_failure_onset` fields.

---

## Task 6: Update API docs

**Files:**
- Modify: `API_DOCS.md` (the "Success Response" and "Response Fields" sections)

- [ ] **Step 1: Update the Success Response example**

Find the `#### Success Response (200)` section in `API_DOCS.md`. Replace its JSON example with:

```json
{
  "success": true,
  "report": {
    "engine_code": "OM651 DE 22 LA",
    "reliability_score": 5,
    "sub_scores": {
      "design": 5,
      "mileage": 5,
      "usage": 7,
      "age": 6
    },
    "typical_failure_onset": {
      "years": 8,
      "mileage_km": 130000
    },
    "summary": "The OM651 has known injector and timing-chain issues (ADAC). At ~106k km the car is approaching the typical failure window. Moderate risk; inspect timing chain and injectors before purchase."
  },
  "error": null
}
```

- [ ] **Step 2: Update the Response Fields table**

Find the `#### Response Fields` table. Replace with:

| Field | Type | Description |
|-------|------|-------------|
| `success` | boolean | Whether the analysis succeeded |
| `report.engine_code` | string | Identified engine code (e.g. "OM651", "N47D20C", "CFCA") |
| `report.reliability_score` | integer | Final 1–10 rating, computed from sub-scores |
| `report.sub_scores.design` | integer | 1–10. Intrinsic engine-family quality |
| `report.sub_scores.mileage` | integer | 1–10. Position in the failure window |
| `report.sub_scores.usage` | integer | 1–10. Ownership/accident/commercial-use history |
| `report.sub_scores.age` | integer | 1–10. Age since first registration |
| `report.typical_failure_onset.years` | integer | Typical age when issues start |
| `report.typical_failure_onset.mileage_km` | integer | Typical odometer reading when issues start |
| `report.summary` | string | 2-3 sentence verdict with cited sources |
| `error` | string or null | Error message if the analysis failed |

- [ ] **Step 3: Commit**

```bash
git add API_DOCS.md
git commit -m "$(cat <<'EOF'
Update API docs for structured scoring response

Documents the new sub_scores and typical_failure_onset fields in the
/api/analyze response. The reliability_score field is retained but is
now computed from the sub-scores server-side.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Push**

```bash
git push origin master
```

---

## Task 7: Update `PROGRESS.md`

**Files:**
- Modify: `PROGRESS.md` (the Progress checklist)

- [ ] **Step 1: Add a completed entry**

Find the Progress section in `PROGRESS.md`. After the existing `- [x] Deployed on Vercel` line, add:

```markdown
- [x] Structured scoring rubric (design/mileage/usage/age sub-scores + failure-window)
```

- [ ] **Step 2: Commit and push**

```bash
git add PROGRESS.md
git commit -m "$(cat <<'EOF'
Mark structured scoring rubric as complete in PROGRESS.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git push origin master
```
