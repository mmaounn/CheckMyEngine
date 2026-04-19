# CheckMyEngine — Development Progress

## Project Goal
Public API that accepts car vehicle data (make, model, year, mileage, specs) and returns engine reputation/reliability analysis powered by Claude API.

## Tech Stack
- **Language:** Python 3.11+
- **Framework:** FastAPI (async, auto-docs, validation)
- **AI Backend:** Anthropic Claude API (claude-sonnet-4-6)
- **Deployment:** Ubuntu with uvicorn + nginx

## Files
- `main.py` — FastAPI app with `/analyze` and `/health` endpoints
- `engine_analyzer.py` — Claude API integration with source-referenced prompt
- `models.py` — Pydantic models (request, response, EngineReport, KnownIssue)
- `config.py` — Settings loaded from .env
- `requirements.txt` — Python dependencies
- `.env.example` — API key template

## Progress
- [x] Project initialized
- [x] Tech stack chosen (Python + FastAPI + Claude API)
- [x] Project structure setup
- [x] Pydantic models (AnalyzeRequest, EngineReport, KnownIssue, AnalyzeResponse)
- [x] Claude prompt — demands cited sources (TSBs, recalls, ADAC, TÜV, NHTSA, forums)
- [x] Core `/analyze` endpoint
- [x] Summary constrained to 2-3 lines max
- [ ] Test locally
- [ ] Deployment config (systemd, nginx)
- [ ] API key auth for public consumers
- [ ] Rate limiting

## Key Design Decisions
- Claude prompt explicitly forbids fabricating source IDs — says "widely reported" if unsure
- Reliability score 1-10 based on full engine production run, not just the one car
- Mileage assessed against engine-specific lifespan expectations
- Summary kept to 2-3 short sentences for quick buyer decisions
