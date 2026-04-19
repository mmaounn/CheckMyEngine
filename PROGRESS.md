# CheckMyEngine — Development Progress

## Project Goal
Public API that accepts car vehicle data and returns engine reputation analysis powered by Claude API.

## Tech Stack
- **Language:** Python 3.11+
- **Framework:** FastAPI
- **AI Backend:** Claude Sonnet 4.6
- **Hosting:** Vercel (serverless)
- **Source:** github.com/mmaounn/CheckMyEngine (public)
- **Live URL:** https://check-my-engine.vercel.app/api/analyze

## Progress
- [x] Core API with /analyze and /health endpoints
- [x] Claude prompt with sourced engine analysis (ADAC, TUV, KBA, TSBs)
- [x] Brief summary (2-3 sentences, ~280 chars)
- [x] Auto-detect response language from input
- [x] Manual language override (en/de)
- [x] API key authentication (X-API-Key header)
- [x] Deployed on Vercel
- [x] Pushed to GitHub
- [ ] Rate limiting
- [ ] Usage tracking / analytics
- [ ] More language support beyond en/de

## API Usage
```
POST /api/analyze
Header: X-API-Key: <your-key>
Header: Content-Type: application/json
Body: {"vehicle_data": "...", "language": "en|de|null"}
```

## Cost
~$0.005 per query (~$5 per 1,000 queries)
