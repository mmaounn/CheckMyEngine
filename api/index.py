import json
import os

import anthropic
from fastapi import FastAPI, HTTPException, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

# --- Auth ---

API_KEY_HEADER = APIKeyHeader(name="X-API-Key")


def verify_api_key(api_key: str = Security(API_KEY_HEADER)) -> str:
    valid_keys = set(os.environ.get("CME_API_KEYS", "").split(","))
    if api_key not in valid_keys:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return api_key

# --- Models ---

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


class ExtractedSpecs(BaseModel):
    engine_variant_code: str | None = Field(default=None, description="Engine variant code printed on the Fahrzeugschein (e.g. 'ACGLCF1', 'CFGC').")
    displacement_ccm: int | None = Field(default=None, ge=0, description="Cylinder displacement in cm³.")
    power_kw: int | None = Field(default=None, ge=0, description="Rated power in kilowatts.")
    first_registration: str | None = Field(default=None, description="Date of first registration in YYYY-MM-DD.")
    emissions_class: str | None = Field(default=None, description="Emissions class (e.g. 'EURO5', 'EURO6').")


def compute_reliability_score(sub_scores: "SubScores") -> int:
    """Weighted-average of sub-scores, rounded and clamped to [1, 10]."""
    raw = (
        0.4 * sub_scores.design
        + 0.3 * sub_scores.mileage
        + 0.2 * sub_scores.usage
        + 0.1 * sub_scores.age
    )
    return max(1, min(10, round(raw)))


class EngineReport(BaseModel):
    engine_code: str = Field(description="The identified engine code/designation (e.g. OM651)")
    reliability_score: int = Field(description="Final 1-10 score computed from sub-scores (weighted average).", ge=1, le=10)
    sub_scores: SubScores = Field(description="Breakdown of the rubric factors that produce the final score.")
    typical_failure_onset: FailureOnset = Field(description="Point estimate of when issues typically start for this engine family.")
    summary: str = Field(description="2-3 sentence verdict on engine reputation, reliability, and mileage risk")


class AnalyzeResponse(BaseModel):
    success: bool
    report: EngineReport | None = None
    error: str | None = None


# --- Claude Prompt ---

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


# --- Engine Analyzer ---

LANGUAGE_INSTRUCTIONS = {
    "en": "Write the summary in English.",
    "de": "Schreibe die Zusammenfassung auf Deutsch.",
}

AUTO_DETECT_INSTRUCTION = "Write the summary in the same language as the vehicle listing input."


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


# --- FastAPI App ---

app = FastAPI(
    title="CheckMyEngine API",
    description="Analyze car engine reputation and reliability from vehicle listing data.",
    version="1.0.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze(request: AnalyzeRequest, _key: str = Security(verify_api_key)):
    """Analyze a vehicle's engine reliability based on listing data."""
    try:
        report = await analyze_engine(request.vehicle_data, request.language)
        return AnalyzeResponse(success=True, report=report)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=502, detail=f"Failed to parse engine analysis: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/health")
async def health():
    return {"status": "ok"}


