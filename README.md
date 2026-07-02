# Transaction Monitoring POC — Data Ingestion + AI-Assisted L1 Review

A runnable proof of concept for the architecture we discussed: synthetic
transaction data → deterministic rules engine → AI copilot (confidence
score + rule breakdown + summary) → L1 analyst review with human-in-the-loop
disposition and an audit trail.

## Files

| File | Role |
|---|---|
| `generate_data.py` | Creates synthetic customers, accounts, and transactions in `data/` (60 customers, ~1,800 transactions), with 14 known AML typologies deliberately injected across 8 rule categories, so you can verify detection against ground truth. |
| `rules_engine.py` | Deterministic, explainable rule logic — 8 rules (`R01`–`R08`), described in full below. Scans transactions per customer, produces alerts with evidence and a rule-based base score, written to `output/alerts.json`. |
| `ai_copilot.py` | The AI layer. Takes each alert and asks Claude (or falls back to a deterministic mock if no API key) to: adjust the base score by at most ±10 points with a stated reason, explain each triggered rule in plain language, write a short summary, and recommend an action. Writes `output/alerts_with_ai.json`. |
| `output/case_review_ui.html` | Standalone L1 analyst case-review screen. Open it directly in a browser — no server needed. Shows the alert queue, the AI's assessment (score, rule breakdown, summary), and lets you record a human disposition (close / request info / escalate), including an override flag if the analyst disagrees with the AI. |

## Rule reference (R01–R08)

Each rule is deterministic and produces a weighted "hit" with concrete evidence — this is what keeps the alert explainable to an analyst and defensible to a compliance reviewer, as opposed to a black-box score.

| Rule | Name | Weight | What it detects | Why it matters |
|---|---|---|---|---|
| **R01** | Structuring (CTR avoidance) | 35 | 3+ deposits of $9,000–$9,999 within a 5-day window | Classic technique to keep each deposit under the $10,000 Currency Transaction Report threshold while moving large sums overall |
| **R02** | Velocity spike | 20 | Transaction count in any 48-hour window is ≥10 and ≥4x the customer's own baseline rate | Sudden bursts of activity — well outside a customer's normal pattern — often mark account takeover, mule activity, or a scheme ramping up |
| **R03** | High-risk jurisdiction | 30 | Any transaction with a counterparty in a watch-listed country | Funds flowing to/from sanctioned or high-risk jurisdictions is one of the most direct AML red flags |
| **R04** | Rapid movement of funds | 30 | A large inbound transfer (≥$10k) with ≥75% of it moved back out within 48 hours | "Pass-through" behavior — the account is being used as a conduit rather than for genuine banking, common in mule networks |
| **R05** | Round-tripping | 25 | A large outbound transfer (≥$8k) with a similar amount (±10%) returning within 10 days | Circular fund flow — money leaves and comes back with no real economic purpose, a layering technique to create confusing audit trails |
| **R06** | Dormant account reactivation | 15 | 45+ days of inactivity followed by 4+ transactions within 5 days | Dormant accounts are often "warmed up" and used briefly for a single scheme, then abandoned again |
| **R07** | Cash-intensive activity | 20 | Cash transactions ≥45% of total volume AND ≥1.2x the customer's expected monthly volume | Disproportionate cash use relative to a customer's known profile is a common placement-stage laundering indicator |
| **R08** | Multi-account fragmentation | 30 | 3+ near-CTR-threshold deposits split across 2+ of the customer's own accounts | A structuring variant specifically designed to evade single-account monitoring by spreading activity across accounts at the same bank |

Multiple rules can fire on the same alert (e.g. a rapid-movement transfer that also happens to go to a high-risk country triggers both R03 and R04) — the base score sums the weights of every rule that fires, capped at 100.



```bash
python3 generate_data.py      # -> data/customers.csv, accounts.csv, transactions.csv
python3 rules_engine.py       # -> output/alerts.json
python3 ai_copilot.py         # -> output/alerts_with_ai.json
```

Then open `output/case_review_ui.html` in a browser.

To use the real Claude API instead of the mock responder, set an environment
variable before running the copilot step:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python3 ai_copilot.py
```

The mock responder mirrors the exact JSON schema Claude is prompted to
return, so the UI and downstream logic don't change either way — you can
demo the full flow with zero external dependencies, then flip on the real
model when you're ready.

## How this maps to the design

- **Ingestion**: `generate_data.py` stands in for a real core-banking feed.
  In production this would be replaced by a batch/streaming loader (Kafka,
  file drops, DB CDC) landing into the same customer/account/transaction
  shape.
- **Rules engine**: deliberately deterministic and simple. This is the
  auditable "detection" layer — real systems (Actimize, SAS AML, Verafin,
  or an in-house engine) work the same way: rule hits, weights, evidence.
- **AI copilot**: does not replace the rules engine or invent a score from
  nothing. It takes the deterministic base score and rule evidence as
  input, is only allowed to nudge the score within a bounded range with a
  stated reason, and turns structured rule output into something a human
  can read in seconds. This keeps the score defensible in a model-risk /
  compliance review rather than being an opaque LLM number.
- **Human-in-the-loop**: the analyst always makes the final call in the UI.
  Every decision — including whether it agreed with or overrode the AI's
  suggestion — is captured with a timestamp, which is the seed of a real
  audit trail and also becomes your feedback dataset for tuning rule
  thresholds and prompts over time.

## Extending this into something closer to production

- Swap `generate_data.py`'s output for a real (masked/synthetic) core-banking
  extract with the same schema.
- Persist alerts and analyst decisions to a real database instead of JSON
  files, and expose the case-review UI through a backend (FastAPI is a
  natural fit given `ai_copilot.py` is already plain Python).
- Add a feedback loop job that periodically reviews analyst overrides to
  suggest rule threshold tuning.
- Layer in SAR (Suspicious Activity Report) drafting assistance once a case
  is escalated, using the same summary the copilot already generates.
- Add role-based access control and a full audit log store (who viewed
  what, when, and what the AI showed them at the time) — expected by bank
  compliance/audit functions even for a pilot.
