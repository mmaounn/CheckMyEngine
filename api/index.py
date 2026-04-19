import json
import os

import anthropic
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

# --- Models ---

class AnalyzeRequest(BaseModel):
    vehicle_data: str = Field(
        ...,
        description="Free-text vehicle listing string with specs (make, model, year, mileage, etc.)",
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


class KnownIssue(BaseModel):
    issue: str = Field(description="Description of the known issue")
    severity: str = Field(description="low, medium, or high")
    source: str = Field(description="Where this information comes from (e.g. TSB number, recall ID, forum, publication)")


class EngineReport(BaseModel):
    engine_code: str = Field(description="The identified engine code/designation (e.g. OM651)")
    engine_family: str = Field(description="Engine family or series name")
    manufacturer: str = Field(description="Engine manufacturer")
    reliability_rating: str = Field(description="Rating: excellent, good, average, below_average, poor")
    reliability_score: int = Field(description="Score from 1-10, where 10 is most reliable", ge=1, le=10)
    summary: str = Field(description="Brief 2-3 sentence verdict on this engine's reputation")
    mileage_assessment: str = Field(description="Assessment of the odometer reading relative to this engine's expected lifespan")
    known_issues: list[KnownIssue] = Field(description="Known problems with references")
    recalls: list[str] = Field(description="Relevant recall campaigns with IDs where available")
    maintenance_warnings: list[str] = Field(description="Key maintenance items to check at this mileage")
    sources: list[str] = Field(description="References: publications, databases, TSBs, forums cited")


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
and power output. Be specific (e.g. "OM651 DE 22 LA" not just "diesel engine").

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

5. **Mileage assessment:** Evaluate the odometer reading against this specific engine's \
known lifespan expectations and common failure points at that mileage.

6. **Be concise and direct.** The summary MUST be exactly 2-3 short sentences \
(no more than 3 lines of text). A buyer should be able to read it in 10 seconds. \
No filler, no disclaimers like "I recommend a mechanic" — just the engine verdict.

## Output format

Respond with a JSON object matching this exact schema (no markdown, no code fences):
{
  "engine_code": "string",
  "engine_family": "string",
  "manufacturer": "string",
  "reliability_rating": "excellent|good|average|below_average|poor",
  "reliability_score": 1-10,
  "summary": "string",
  "mileage_assessment": "string",
  "known_issues": [
    {"issue": "string", "severity": "low|medium|high", "source": "string"}
  ],
  "recalls": ["string"],
  "maintenance_warnings": ["string"],
  "sources": ["string"]
}
"""


# --- Engine Analyzer ---

async def analyze_engine(vehicle_data: str) -> EngineReport:
    """Send vehicle data to Claude and get a structured engine reliability report."""
    api_key = os.environ["ANTHROPIC_API_KEY"]
    client = anthropic.AsyncAnthropic(api_key=api_key)

    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    "Analyze the engine in this vehicle listing and produce "
                    "the reliability report as JSON:\n\n"
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
        report = await analyze_engine(request.vehicle_data)
        return AnalyzeResponse(success=True, report=report)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=502, detail=f"Failed to parse engine analysis: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/health")
async def health():
    return {"status": "ok"}


