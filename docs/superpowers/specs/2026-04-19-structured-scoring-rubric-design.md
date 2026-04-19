# Structured Scoring Rubric — Design Spec

**Date:** 2026-04-19
**Status:** Approved, ready for implementation planning
**Author:** CheckMyEngine team

## Problem

Testing the current `/api/analyze` endpoint against variants of the same vehicle (2015 VW Sharan 2.0 TDI, CFGC engine) revealed three correctness issues:

- **Score drift on similar inputs.** Adding or removing metadata flags (`Tuning: Yes`, `Commercial use: Rental`, `Prior damage: Yes`) shifted the score and surfaced different "known issues" across runs, without a principled rubric tying input to score.
- **Missing failure-window information.** The summary tells users *what* is wrong with an engine family but not *when* failures typically start. A buyer cannot tell whether 55k km is "before the danger zone" or "right before it hits."
- **Fabricated recall numbers.** A run produced `KBA-Rückruf 23/2909` — the prompt forbids invented TSB/recall IDs, but the rule is not specific enough to prevent plausible-looking fabrications.

A fourth issue — score drift on *identical* inputs (6 → 7 on two identical requests at `temperature=0`) — was explicitly scoped out. Caching by input hash is the correct fix and will be tracked separately.

## Goals

- Replace the monolithic `reliability_score` with four independently scored sub-dimensions the model fills in, then compute the final score deterministically in Python.
- Add a structured `typical_failure_onset` field so the API tells users *when* risk escalates for this engine family.
- Tighten the system prompt's anti-fabrication rule for recall/TSB IDs and its interpretation of the `Tuning` flag.

## Non-Goals

- Caching or any other mitigation for identical-input non-determinism.
- Frontend changes.
- Rate limiting, usage tracking, new endpoints.
- Changes to authentication or the `/api/health` endpoint.
- A test harness (manual curl testing against the deployed API is sufficient for this iteration).

## Design

### Rubric: four sub-scores (1–10 each)

The system prompt instructs the model to produce four sub-scores. Each has a narrow, one-sentence definition so the model cannot drift between runs.

| Sub-score | Definition | Anchors |
|---|---|---|
| `design` | Intrinsic quality of the engine family — design soundness and severity of its known issues, judged across the full production run, independent of this specific vehicle. | 10 = legendary (e.g. Toyota 2JZ). 7 = solid with minor issues. 5 = average with known headaches. 3 = significant design flaws. 1 = fundamentally broken. |
| `mileage` | How far into this engine family's typical failure window the specific car's odometer sits, expressed as `car_km / typical_failure_onset.mileage_km`. | 10 = <25% of onset. 7 = 50–75% of onset. 5 = at onset (~100%). 3 = 100–130% of onset. 1 = >130% of onset. |
| `usage` | Combined judgment of ownership and condition history: number of owners, commercial/rental/taxi use, prior accidents, chip tuning (only if explicitly noted), unusual wear indicators. | 10 = single owner, private use, no accidents. 7 = typical consumer history. 5 = notable concern (multiple owners, commercial use). 3 = serious concern (rental + accident). 1 = likely abused. |
| `age` | Years since first registration, judged relative to the engine's typical durability. | 10 = <3 years. 7 = 5–8 years. 5 = 10 years. 3 = 15+ years. 1 = 20+ years. |

### Final score formula

Computed in Python from the four sub-scores, not by the model:

```
reliability_score = round(0.4 * design + 0.3 * mileage + 0.2 * usage + 0.1 * age)
```

Clamped to `[1, 10]`. Weights reflect that engine-family quality dominates, current-car wear is the next strongest signal, usage history is a meaningful modifier, and age contributes the least (because a 10-year-old low-mileage garage-kept car is very different from a 10-year-old taxi).

### Structured failure-window field

```
typical_failure_onset: {
  years:       int,   # age in years at which issues typically surface
  mileage_km:  int    # odometer reading at which issues typically surface
}
```

Single point estimate, not a range. Forces the model to commit to a number, which keeps the `mileage` sub-score anchored.

### Response shape

The existing envelope (`{success, report, error}`) is preserved. The `report` object gains `sub_scores` and `typical_failure_onset`:

```json
{
  "success": true,
  "report": {
    "engine_code": "CFGC",
    "reliability_score": 8,
    "sub_scores": {
      "design": 7,
      "mileage": 8,
      "usage": 9,
      "age": 8
    },
    "typical_failure_onset": {
      "years": 9,
      "mileage_km": 150000
    },
    "summary": "..."
  },
  "error": null
}
```

Clients that read only `reliability_score` and `summary` are unaffected. Clients that want the breakdown get it for free.

### Prompt changes

The system prompt is updated in four places:

1. **Output schema.** Model is instructed to emit `{engine_code, sub_scores, typical_failure_onset, summary}`. It does NOT emit `reliability_score` — Python computes it.
2. **Rubric definitions.** Each of the four sub-scores gets its one-sentence definition plus anchor points (from the table above) inline in the prompt.
3. **Tighter anti-fabrication rule.** Replace the current "do not fabricate sources" paragraph with: "If you are not highly confident a specific KBA/TSB/NHTSA ID is real and corresponds to this engine, say 'widely reported by owners' or 'ADAC Pannenstatistik' with a year range instead. Do not invent or guess specific recall numbers — plausible-looking IDs are worse than no ID."
4. **Tuning flag interpretation.** Add: "Treat a `Tuning: Yes` flag as informational only. Do not infer chip tuning or engine remapping unless the tuning note explicitly describes it. Accessory retrofits (trailer hitch, auxiliary heater, audio) are not engine tuning."

### Code changes

One file: `api/index.py`.

- `EngineReport` Pydantic model gains `sub_scores: SubScores` and `typical_failure_onset: FailureOnset` nested models. `reliability_score` stays on the model but is computed, not parsed from the model output.
- New `SubScores` model (four `int` fields, each constrained `ge=1, le=10`).
- New `FailureOnset` model (`years: int`, `mileage_km: int`, both `ge=0`).
- `analyze_engine()` parses the Claude response into an intermediate shape without `reliability_score`, computes the final score from sub-scores, and returns the full `EngineReport`.
- `SYSTEM_PROMPT` updated per the four prompt changes above.

## Validation

Re-run the test matrix from the investigation:

- Four variants of the 2015 VW Sharan 2.0 TDI (CFGC): 155k+rental+accident, 55k+rental+accident, 55k+tuning-flag+accident, 55k clean.
- Identical input run twice (to characterize remaining non-determinism, which is out of scope to fix).
- At least one additional engine family (e.g. a 2013 E220 CDI OM651) to confirm the rubric generalizes.

Success criteria:

- Same-engine-family variants produce the same `design` sub-score (since it's intrinsic to the engine).
- The `mileage` sub-score increases monotonically as km decreases.
- The `usage` sub-score decreases when rental/accident/tuning flags are added.
- No fabricated recall numbers appear in any summary.
- The `Tuning: Yes` flag does not produce "Chiptuning-Verdacht" language when the tuning note only describes accessory retrofits.

## Open questions / future work

- **Caching (scope A).** Hash `(vehicle_data, language, prompt_version)` and short-circuit identical requests. Tracked separately.
- **Score rubric calibration.** The weights (0.4/0.3/0.2/0.1) are a first guess. Revisit after 50+ real-world runs if scores feel systematically too high or low.
- **Failure-window format.** If the single-point estimate proves too coarse in practice, upgrade to a range or an issue-specific list. Deferred until we see real data.
- **Prompt-version tagging.** Include a prompt version identifier in the response to aid debugging across deployments. Deferred.
