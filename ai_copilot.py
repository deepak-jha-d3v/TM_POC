"""
ai_copilot.py
-------------
Takes one alert (rule hits + evidence, produced by rules_engine.py) and asks
Claude to act as an L1 analyst copilot: adjust/explain the confidence score,
restate the triggered rules in plain language, write a short summary, and
suggest a next action.

Design principle: the LLM does NOT invent the base score from nothing. The
rules engine already computed a deterministic, auditable base score from
rule weights. The LLM is only allowed to nudge that score within a small
band and must justify the nudge -- this keeps the number defensible to a
model-risk/compliance reviewer instead of being an opaque LLM guess.

This script calls the real Anthropic API if ANTHROPIC_API_KEY is set in the
environment. Otherwise it falls back to a deterministic mock responder so
the pipeline is runnable end-to-end without network/API access (useful for
demos and for this sandbox).
"""

import json
import os
import sys
import urllib.request

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are an AML transaction-monitoring copilot assisting a Level 1 (L1) \
analyst. You are given a rule-based alert: the customer, the deterministic \
rules that fired, their weighted evidence, and a rule-based base score (0-100, \
already calculated by a separate deterministic engine -- you do not recompute it \
from scratch).

Your job:
1. Propose a final confidence_score: the base score adjusted by AT MOST +/-10 \
points based on contextual factors (customer risk rating, prior alert history, \
whether multiple independent typologies co-occur). State your adjustment_reason.
2. For each triggered rule, write a one-sentence plain-language explanation an \
L1 analyst can read in a few seconds.
3. Write a 2-3 sentence summary of why this alert exists and what it suggests.
4. Recommend one suggested_action: "close_false_positive", "escalate_to_l2", \
or "request_more_info".

Respond ONLY with a single JSON object, no markdown fences, no preamble, matching \
this schema:
{
  "confidence_score": <int 0-100>,
  "confidence_label": <"Low"|"Medium"|"Medium-High"|"High">,
  "base_score": <int, echoed from input>,
  "adjustment_reason": <string, 1 sentence>,
  "rules_explained": [{"rule_id": <string>, "plain_language": <string>}],
  "summary": <string, 2-3 sentences>,
  "suggested_action": <string>
}"""


def call_claude(alert: dict) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    user_content = json.dumps({
        "customer_id": alert["customer_id"],
        "customer_risk_rating": alert["customer_risk_rating"],
        "customer_country": alert["customer_country"],
        "rules_triggered": alert["rules_triggered"],
        "rule_based_score": alert["rule_based_score"],
        "transaction_count_in_window": alert["transaction_count_in_window"],
    }, indent=2)

    if not api_key:
        return mock_response(alert)

    body = json.dumps({
        "model": MODEL,
        "max_tokens": 1000,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_content}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        text = "".join(block["text"] for block in data["content"] if block["type"] == "text")
        text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(text)
    except Exception as e:
        print(f"[ai_copilot] API call failed ({e}); falling back to mock response.", file=sys.stderr)
        return mock_response(alert)


def mock_response(alert: dict) -> dict:
    """Deterministic stand-in for the LLM call, so the POC runs with no API key.
    Mirrors the exact JSON schema Claude is prompted to return."""
    base = alert["rule_based_score"]
    n_rules = len(alert["rules_triggered"])
    risk = alert["customer_risk_rating"]

    adjustment = 0
    reasons = []
    if n_rules >= 2:
        adjustment += 8
        reasons.append(f"{n_rules} independent typologies co-occurring raises concern")
    if risk == "high":
        adjustment += 5
        reasons.append("customer already carries a high KYC risk rating")
    elif risk == "low" and n_rules == 1:
        adjustment -= 5
        reasons.append("single rule hit on an otherwise low-risk, low-history customer")
    adjustment = max(-10, min(10, adjustment))
    final_score = max(0, min(100, base + adjustment))

    label = ("Low" if final_score < 30 else
             "Medium" if final_score < 55 else
             "Medium-High" if final_score < 75 else "High")

    action = ("close_false_positive" if final_score < 30 else
              "request_more_info" if final_score < 60 else
              "escalate_to_l2")

    rules_explained = []
    for r in alert["rules_triggered"]:
        rules_explained.append({
            "rule_id": r["rule_id"],
            "plain_language": f"{r['name']} flagged: {r['evidence']}.",
        })

    rule_names = "; ".join(r["name"] for r in alert["rules_triggered"])
    summary = (
        f"This alert was generated for {alert['customer_name']} ({alert['customer_id']}, "
        f"{risk}-risk customer) after {n_rules} rule(s) fired: {rule_names}. "
        f"Combined with the customer's risk profile, this pattern is "
        f"{'consistent with' if final_score >= 55 else 'only weakly suggestive of'} "
        f"potentially suspicious activity and warrants L1 review."
    )

    return {
        "confidence_score": final_score,
        "confidence_label": label,
        "base_score": base,
        "adjustment_reason": "; ".join(reasons) if reasons else "no contextual adjustment applied",
        "rules_explained": rules_explained,
        "summary": summary,
        "suggested_action": action,
        "_mode": "mock (no ANTHROPIC_API_KEY set)",
    }


if __name__ == "__main__":
    import os
    os.makedirs("output", exist_ok=True)

    if not os.path.exists("output/alerts.json"):
        sys.exit("output/alerts.json not found. Run rules_engine.py first.")

    with open("output/alerts.json") as f:
        alerts = json.load(f)

    enriched = []
    for alert in alerts:
        copilot_output = call_claude(alert)
        alert["ai_copilot"] = copilot_output
        enriched.append(alert)
        print(f"{alert['alert_id']:<16} base={copilot_output['base_score']:<4} "
              f"final={copilot_output['confidence_score']:<4} "
              f"({copilot_output['confidence_label']:<12}) "
              f"-> {copilot_output['suggested_action']}")

    with open("output/alerts_with_ai.json", "w") as f:
        json.dump(enriched, f, indent=2)
