# Transaction Monitoring POC — Data Ingestion + AI-Assisted L1 Review

A runnable proof of concept: synthetic transaction data (fiat **and** crypto,
including private-wallet activity) → deterministic rules engine (12 rules) →
AI copilot (confidence score + rule breakdown + summary) → L1 analyst review
with human-in-the-loop disposition and a per-submission audit trail written
directly to `L1_Audit_tracker.csv`.

## Files

| File | Role |
|---|---|
| `generate_data.py` | Creates synthetic customers, accounts, transactions (fiat + crypto), and a customer-profile-update feed in `data/` — **90 customers, ~3,200 transactions** (~470 of them crypto), with typologies for all 12 rules deliberately injected across 34 customers, so you can verify detection against ground truth. |
| `rules_engine.py` | Deterministic, explainable rule logic — 12 rules (`R01`–`R12`), described below. Scans transactions per customer, produces alerts with evidence and a rule-based base score, written to `output/alerts.json`. |
| `ai_copilot.py` | The AI layer. Takes each alert and asks Claude (or falls back to a deterministic mock if no API key) to adjust the base score by at most ±10 points with a stated reason, explain each triggered rule in plain language, write a short summary, and recommend an action. Writes `output/alerts_with_ai.json`. |
| `enrich_ui_data.py` | Attaches a handful of the most evidentially interesting transactions (large amounts, crypto, private-wallet moves, high-risk corridors, cash) to each alert for display in the UI. Writes `output/alerts_for_ui.json`. |
| `build_ui.py` | Injects the enriched alert data + rule catalog into `ui_template.html` to produce the final standalone `output/case_review_ui.html`. |
| `output/case_review_ui.html` | Standalone L1 analyst case-review screen, branded **SARA.ai**. Open it directly in a browser — no server needed. Alert queue with search/risk/rule/crypto filters, an AI assessment panel with a dedicated score-adjustment card, **accordion cards** for each triggered rule (collapsed by default, expand for the plain-language explanation + evidence), a supporting-transactions table, and analyst disposition capture that commits straight to `L1_Audit_tracker.csv` on submit. |

## Rule reference (R01–R12)

R01–R10 are the ten rules requested for this build. R11–R12 are carried over
from the original POC (round-tripping, multi-account fragmentation) and kept
as bonus coverage since they were already implemented and working — nothing
was removed, only added to.

| Rule | Name | Weight | What it detects | Why it matters |
|---|---|---|---|---|
| **R01** | Detection of Structuring | 35 | 3+ deposits of $9,000–$9,999 within a 5-day window | Classic technique to keep each deposit under the $10,000 CTR threshold while moving large sums overall |
| **R02** | Customer Details Updated Before a Large Transaction | 20 | A KYC/contact detail (address, phone, email, beneficiary) changed within 72h before a transaction ≥$15,000 | Common precursor to account takeover, or a mule/shell account being repurposed for a single large movement |
| **R03** | Unusual Spending Pattern | 20 | A transaction ≥3.5 standard deviations above the customer's own historical average | Catches a single large anomalous movement relative to *that customer's* own baseline, not a generic threshold |
| **R04** | Low Buyers Diversity | 25 | 6+ crypto sales where unique buyer wallets make up ≤35% of the transaction count | Repeated sales into the same tiny handful of wallets is consistent with wash trading or a closed layering ring |
| **R05** | Disproportionate Flow-Through | 30 | A large inbound transfer (≥$10k) with ≥75% of it moved back out within 48 hours | "Pass-through" behavior — the account is being used as a conduit, common in mule networks |
| **R06** | High-Risk Countries | 30 | Any transaction (fiat or crypto-exchange) with a counterparty in a watch-listed country | Funds flowing to/from sanctioned or high-risk jurisdictions is one of the most direct AML red flags |
| **R07** | Immediate Withdrawal to Private Wallets | 30 | Crypto credited to an exchange wallet, then ≥85% of it withdrawn to an unhosted **private wallet** within 6 hours | Removes funds from any custodial, monitorable environment almost immediately after entry |
| **R08** | Cash Transactions | 20 | Cash transactions ≥45% of total volume AND ≥1.2x the customer's expected monthly volume | Disproportionate cash use relative to a customer's known profile is a common placement-stage indicator |
| **R09** | Dormant Accounts | 15 | 45+ days of inactivity followed by 4+ transactions within 5 days | Dormant accounts are often "warmed up" and used briefly for a single scheme, then abandoned again |
| **R10** | Frequent Conversions Crypto-FIAT or FIAT-Crypto | 25 | 6+ crypto↔fiat conversions within a 30-day window | Repeatedly hopping asset types is a layering technique that obscures the trail without moving funds geographically |
| **R11** *(bonus)* | Round-Tripping | 25 | A large outbound transfer (≥$8k) with a similar amount (±10%) returning within 10 days | Circular fund flow with no real economic purpose — a layering technique |
| **R12** *(bonus)* | Multi-Account Fragmentation | 30 | 3+ near-CTR-threshold deposits split across 2+ of the customer's own accounts | Structuring variant designed to evade single-account monitoring |

Multiple rules can fire on the same alert — the base score sums the weights
of every rule that fires, capped at 100.

## Crypto & private-wallet data model

`data/transactions.csv` now carries additional columns beyond the original
fiat schema:

| Column | Meaning |
|---|---|
| `asset_type` | `FIAT` or `CRYPTO` |
| `crypto_symbol` | `BTC` / `ETH` / `USDT` / `SOL` / `XRP` (empty for fiat) |
| `wallet_type` | `exchange_wallet` or `private_wallet` (unhosted) for crypto legs |
| `buyer_id` | Counterparty buyer wallet on crypto sales (used by R04) |
| `is_conversion` | Flags a crypto↔fiat conversion leg (used by R10) |

`data/customer_profile_updates.csv` is a new feed: `update_id`,
`customer_id`, `update_type` (address/phone/email/beneficiary_details),
`update_timestamp` — used by R02.

## Run it end to end

```bash
python3 generate_data.py      # -> data/customers.csv, accounts.csv, transactions.csv, customer_profile_updates.csv
python3 rules_engine.py       # -> output/alerts.json
python3 ai_copilot.py         # -> output/alerts_with_ai.json
python3 enrich_ui_data.py     # -> output/alerts_for_ui.json
python3 build_ui.py           # -> output/case_review_ui.html
```

Then open `output/case_review_ui.html` in a browser. No build step, no
server — everything (data + logic) is embedded in the single HTML file.

To use the real Claude API instead of the mock responder for the copilot
step, set an environment variable before running it:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python3 ai_copilot.py
```

The mock responder mirrors the exact JSON schema Claude is prompted to
return, so the UI and downstream logic don't change either way — you can
demo the full flow with zero external dependencies, then flip on the real
model when you're ready.

## UI features

- **SARA.ai branding** — the header shows a single "SARA.ai" logo mark; no
  duplicate product name elsewhere in the header. The descriptive subtitle
  ("POC · synthetic data incl. crypto & private wallets · human-in-the-loop")
  stays underneath it.
- **Accordion rule cards** — each triggered rule collapses to its name and
  weight by default; expanding it reveals the AI's plain-language
  explanation plus the raw rule evidence, keeping the page uncluttered when
  several rules fire on one alert.
- **Score adjustment panel** — the AI copilot's confidence score is no
  longer folded into the general summary text. It has its own card showing
  the base→adjusted score delta (with a color-coded up/down/no-change tag)
  and a short, 3-bullet justification, so an analyst can see *why* the score
  moved without reading a full paragraph.
- **Filters** — free-text search (name/ID), risk-rating toggle, a
  "crypto-involved only" toggle, and per-rule filter chips so you can
  isolate, say, every alert where R07 (private-wallet withdrawal) fired.
- **Supporting transactions table** — the most evidentially interesting
  transactions for the selected customer (large amounts, crypto, private
  wallet moves, cash, high-risk corridors), so the analyst isn't just
  reading prose evidence.
- **Stage → Submit → commit disposition flow** — clicking Close / Request
  More Info / Escalate only stages the analyst's choice; nothing is saved
  until **Submit decision** is clicked. There is no manual export button
  anywhere in the UI.
- **Direct-to-file audit trail** — on submit, the browser's File System
  Access API is used to write (or append to, on subsequent submits) a file
  named **`L1_Audit_tracker.csv`** on the analyst's machine — one row per
  submitted decision, including the alert, AI base/adjusted score, the
  analyst's action, note, override flag, and timestamp. The decision is
  only marked as committed in the UI once that write succeeds. Browsers
  without the File System Access API (Firefox, Safari) fall back to
  automatically re-saving the same `L1_Audit_tracker.csv` filename on each
  submit.

## How this maps to the design

- **Ingestion**: `generate_data.py` stands in for a real core-banking +
  crypto-custodian feed. In production this would be replaced by a
  batch/streaming loader (Kafka, file drops, DB CDC) landing into the same
  customer/account/transaction shape, plus a KYC/CRM feed for profile
  updates.
- **Rules engine**: deliberately deterministic and simple. This is the
  auditable "detection" layer — real systems (Actimize, SAS AML, Verafin,
  or an in-house engine) work the same way: rule hits, weights, evidence.
- **AI copilot**: does not replace the rules engine or invent a score from
  nothing. It takes the deterministic base score and rule evidence as
  input, is only allowed to nudge the score within a bounded range with a
  stated reason, and turns structured rule output into something a human
  can read in seconds. The score's justification is now surfaced in its own
  UI panel, separate from the general case summary.
- **Human-in-the-loop**: the analyst always makes the final call in the UI.
  A decision is staged (button click) and only becomes final once
  explicitly submitted — at which point it's written directly to
  `L1_Audit_tracker.csv` as the audit record, rather than requiring the
  analyst to remember to run a separate export step. This is the seed of a
  real audit log and a feedback dataset for tuning rule thresholds and
  prompts over time.

## Extending this into something closer to production

- Swap `generate_data.py`'s output for a real (masked/synthetic)
  core-banking + crypto-custodian extract with the same schema.
- Persist alerts, profile-update events, and analyst decisions to a real
  database instead of JSON/CSV files, and expose the case-review UI through
  a backend (FastAPI is a natural fit given `ai_copilot.py` is already
  plain Python). A backend would also remove the current reliance on each
  analyst's local browser (File System Access API) for the audit file.
- Wire R04/R07/R10 to real exchange/custody APIs for wallet attribution
  (exchange-hosted vs. unhosted) rather than the synthetic `wallet_type`
  flag used here.
- Add a feedback loop job that periodically reviews analyst overrides to
  suggest rule threshold tuning.
- Layer in SAR (Suspicious Activity Report) drafting assistance once a case
  is escalated, using the same summary the copilot already generates.
- Add role-based access control and a full audit log store (who viewed
  what, when, and what the AI showed them at the time) — expected by bank
  compliance/audit functions even for a pilot.

## Recent UI changes (this revision)

Changes made to `output/case_review_ui.html` since the original build:

1. **Score adjustment panel** — the AI's base→adjusted score explanation was
   split out of the general summary text into its own card with a
   color-coded delta tag and a crisp, 3-bullet justification.
2. **Removed the filler phrase "Taken together"** from the start of every
   AI-generated justification sentence (in `adjustment_reason`) — the
   sentence now reads "These factors supported…" / "These factors did not
   clear…" directly.
3. **Stage → Submit flow** — the disposition buttons (Close / Request More
   Info / Escalate) now only stage a choice. A new **Submit decision**
   button commits it; it's disabled until a choice is staged.
4. **Removed all export functionality** — both the "Export decisions (CSV)"
   and "Export audit log (JSON)" header buttons, and their underlying
   functions, were removed. There is no manual export step anywhere in the
   UI.
5. **Direct-to-CSV audit trail** — Submit now writes straight to
   `L1_Audit_tracker.csv` on the analyst's machine (via the File System
   Access API, with a same-filename download fallback for browsers that
   don't support it), instead of using in-app storage plus a manual export.
6. **Rebranding** — the header now shows a single "SARA.ai" logo mark in
   the blue badge; the duplicate product-name heading next to it was
   removed, and the descriptive subtitle line was kept unchanged.
