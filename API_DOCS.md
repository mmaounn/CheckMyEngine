# CheckMyEngine API Documentation

## Overview

CheckMyEngine analyzes car engine reputation and reliability based on vehicle listing data. It identifies the exact engine code, provides a reliability score, and gives a brief sourced verdict — helping buyers make informed decisions.

## Base URL

```
https://check-my-engine.vercel.app
```

## Authentication

All requests require an API key sent via the `X-API-Key` header.

```
X-API-Key: your-api-key-here
```

Contact the API administrator to obtain your key.

---

## Endpoints

### POST /api/analyze

Analyze a vehicle's engine reliability.

#### Headers

| Header | Required | Description |
|--------|----------|-------------|
| `X-API-Key` | Yes | Your API key |
| `Content-Type` | Yes | Must be `application/json` |

#### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `vehicle_data` | string | Yes | Vehicle listing text (min 10 characters). Can be in any language. |
| `language` | string | No | Response language: `"en"` for English, `"de"` for German. If omitted, auto-detects from input language. |

#### Example Request

```bash
curl -X POST https://check-my-engine.vercel.app/api/analyze \
  -H "X-API-Key: your-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{
    "vehicle_data": "Mercedes-Benz E-Klasse E 220 CDI BlueEfficiency\nBuild year: 2013\nFirst registration: 07/2013\nOdometer reading: 106,698 km\nFuel type: Diesel\nHorsepower: 125 kW / 170 HP\nCylinder capacity: 2,143 ccm\nGear box: Automatic\nBody type: Cabrio\nTotal number of owners: 2\nCountry of origin: DE\nEnvironmental class: EURO 5\nCO2 Emissions: 130g/km"
  }'
```

#### Example Request (German input, auto-detected response)

```bash
curl -X POST https://check-my-engine.vercel.app/api/analyze \
  -H "X-API-Key: your-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{
    "vehicle_data": "BMW 320d E90\nBaujahr: 2010\nLaufleistung: 180.000 km\nKraftstoff: Diesel\nLeistung: 184 PS\nHubraum: 1.995 ccm"
  }'
```

#### Example Request (Force English response from German input)

```bash
curl -X POST https://check-my-engine.vercel.app/api/analyze \
  -H "X-API-Key: your-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{
    "vehicle_data": "BMW 320d E90\nBaujahr: 2010\nLaufleistung: 180.000 km\nKraftstoff: Diesel\nLeistung: 184 PS\nHubraum: 1.995 ccm",
    "language": "en"
  }'
```

#### Success Response (200)

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

#### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `success` | boolean | Whether the analysis succeeded |
| `report.engine_code` | string | Identified engine code (e.g. "OM651", "N47D20C", "CFCA") |
| `report.reliability_score` | integer | Final 1–10 rating, computed as a weighted average of sub-scores |
| `report.sub_scores.design` | integer | 1–10. Intrinsic engine-family quality |
| `report.sub_scores.mileage` | integer | 1–10. Position within the typical failure window |
| `report.sub_scores.usage` | integer | 1–10. Ownership, commercial-use, and accident history |
| `report.sub_scores.age` | integer | 1–10. Age since first registration |
| `report.typical_failure_onset.years` | integer | Typical age in years when issues start for this engine |
| `report.typical_failure_onset.mileage_km` | integer | Typical odometer reading when issues start |
| `report.summary` | string | 2-3 sentence verdict with cited sources |
| `error` | string or null | Error message if the analysis failed |

The final score is computed server-side as `round(0.4*design + 0.3*mileage + 0.2*usage + 0.1*age)`, clamped to `[1, 10]`.

#### Reliability Score Guide

| Score | Rating | Meaning |
|-------|--------|---------|
| 9-10 | Excellent | Legendary reliability (e.g. Toyota 2JZ) |
| 7-8 | Good | Solid engine with minor known issues |
| 5-6 | Average | Some known problems, maintenance-dependent |
| 3-4 | Below Average | Significant known issues, high repair risk |
| 1-2 | Poor | Fundamentally flawed design |

---

### GET /api/health

Health check endpoint. No authentication required.

#### Example

```bash
curl https://check-my-engine.vercel.app/api/health
```

#### Response

```json
{"status": "ok"}
```

---

## Error Responses

| Status | Meaning |
|--------|---------|
| 401 | Missing or invalid API key |
| 422 | Invalid request body (e.g. vehicle_data too short) |
| 500 | Internal server error |
| 502 | Failed to parse engine analysis |

---

## Input Tips

For best results, include as much of the following as possible in `vehicle_data`:

- **Make and model** (e.g. "Mercedes-Benz E 220 CDI")
- **Build year**
- **Odometer reading** (in km)
- **Fuel type** (Diesel, Petrol, etc.)
- **Horsepower** (kW and/or HP)
- **Cylinder capacity** (ccm)
- **Gearbox type**
- **Number of owners**
- **Prior damage / accident history**
- **Country of origin**
- **Environmental class** (EURO 5, EURO 6, etc.)

The input can be in **any language** — the API will parse it regardless.

---

## Rate Limits

Please keep usage reasonable. Excessive requests may be throttled or your key may be revoked.

---

## Support

For API keys, issues, or questions, contact the API administrator.
