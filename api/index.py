import json
import os

import anthropic
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

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
    language: str = Field(
        default="en",
        description="Response language: 'en' for English, 'de' for German",
        examples=["en", "de"],
    )


class EngineReport(BaseModel):
    engine_code: str = Field(description="The identified engine code/designation (e.g. OM651)")
    reliability_score: int = Field(description="Score from 1-10, where 10 is most reliable", ge=1, le=10)
    summary: str = Field(description="2-3 sentence verdict on engine reputation, reliability, and mileage risk")


class AnalyzeResponse(BaseModel):
    success: bool
    report: EngineReport | None = None
    error: str | None = None


# --- Claude Prompt ---

SYSTEM_PROMPT = """\
You are an automotive engine reliability analyst. Your job is to identify the \
exact engine in a vehicle listing and produce a factual reliability report.

## Rules

1. **Identify the engine code** from the vehicle make, model, year, displacement, \
and power output. Be specific (e.g. "OM651 DE 22 LA" not just "diesel engine"). \
The input listing may be in any language (English, German, etc.) — parse it regardless.

2. **Every claim must be traceable.** For each known issue, recall, or rating you \
provide, cite the source:
   - NHTSA recall campaign numbers (e.g. "NHTSA 19V-123")
   - Manufacturer TSB numbers (e.g. "Mercedes TSB LI07.00-P-054321")
   - KBA (German Federal Motor Transport Authority) recall IDs
   - Published reliability studies (e.g. "TÜV Report 2023", "ADAC Pannenstatistik 2022", \
"Consumer Reports 2020 Annual Auto Issue")
   - Well-known automotive engineering references (e.g. specific articles from \
"Engine Technology International", SAE papers)
   - Major enthusiast/owner forums ONLY for widely corroborated issues \
(e.g. "MBWorld.org — multiple owner reports of injector seal failure at 80-120k km")

3. **Do NOT fabricate sources.** If you are not confident a specific TSB/recall number \
is real, describe the issue and say "source: widely reported by owners" or \
"source: general industry knowledge" instead of inventing a number.

4. **Reliability score (1-10):** Base this on the engine's track record across its \
full production run, not just this one car. 10 = legendary reliability (e.g. Toyota 2JZ), \
1 = fundamentally flawed design. Most engines land between 4-8.

5. **BREVITY IS ABSOLUTELY MANDATORY.** The summary must be MAXIMUM 3 short simple sentences. \
Total length must be under 280 characters (like a tweet). Example of correct length: \
"The OM651 is a known problem engine with injector and timing chain issues (ADAC 2019). \
At 106k km, major failures are statistically imminent. High risk purchase." \
Do NOT write long compound sentences. Do NOT list specific part names. Just the verdict.

## Output format

Respond with ONLY this JSON (no markdown, no code fences):
{
  "engine_code": "string",
  "reliability_score": 1-10,
  "summary": "2-3 sentences max, with inline source references"
}
"""


# --- Engine Analyzer ---

LANGUAGE_INSTRUCTIONS = {
    "en": "Write the summary in English.",
    "de": "Schreibe die Zusammenfassung auf Deutsch.",
}


async def analyze_engine(vehicle_data: str, language: str = "en") -> EngineReport:
    """Send vehicle data to Claude and get a structured engine reliability report."""
    api_key = os.environ["ANTHROPIC_API_KEY"]
    client = anthropic.AsyncAnthropic(api_key=api_key)
    lang_instruction = LANGUAGE_INSTRUCTIONS.get(language, LANGUAGE_INSTRUCTIONS["en"])

    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
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
    return EngineReport(**data)


# --- FastAPI App ---

app = FastAPI(
    title="CheckMyEngine API",
    description="Analyze car engine reputation and reliability from vehicle listing data.",
    version="1.0.0",
)


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze(request: AnalyzeRequest):
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


