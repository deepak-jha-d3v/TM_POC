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
whether multiple independent typologies co-occur). Write an adjustment_reason \
of 3-4 sentences that an L1 analyst or a compliance/model-risk reviewer could \
rely on: (a) name which specific contextual factors you weighed and how each one \
pushed the score up, down, or was considered but not weighted, (b) explicitly \
state the point delta and confirm it falls within the +/-10 band, and (c) explain \
in plain terms why that net effect makes the alert more or less likely to reflect \
genuine suspicious activity. Do not just restate the rule evidence -- that is \
covered separately in rules_explained.
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
  "adjustment_reason": <string, 3-4 sentences, see instructions above>,
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


def build_adjustment_reason(alert: dict, base: int, final: int, n_rules: int, risk: str) -> str:
    """Builds a multi-sentence, analyst/compliance-reviewer-grade explanation of
    why the AI copilot moved (or didn't move) the score away from the
    deterministic rule-based base score. Distinct from rules_explained, which
    covers rule evidence -- this covers the *contextual* reasoning layered on
    top of that evidence."""
    delta = final - base
    rule_names = [r["name"] for r in alert["rules_triggered"]]

    if n_rules >= 2:
        if n_rules == 2:
            combo = f"{rule_names[0]} and {rule_names[1]}"
        else:
            combo = ", ".join(rule_names[:-1]) + f", and {rule_names[-1]}"
        s1 = (f"The rule-based base score of {base} already reflects {n_rules} independently "
              f"triggered typologies ({combo}) rather than a single isolated hit, which matters "
              f"because two unrelated detection rules firing on the same customer in the same "
              f"window is a materially stronger signal than either rule alone -- it lowers the "
              f"chance this is coincidental account activity.")
    else:
        s1 = (f"The rule-based base score of {base} reflects a single triggered typology, "
              f"{rule_names[0]}, with no other rule corroborating it elsewhere in the "
              f"transaction window, which on its own is weaker evidence of coordinated "
              f"suspicious activity.")

    if risk == "high":
        s2 = ("This customer already carries a high KYC risk rating, so the same rule evidence "
              "is weighted more heavily than it would be for a lower-risk customer -- prior due "
              "diligence has already flagged this relationship for closer scrutiny, and new "
              "alert activity on a high-risk profile is treated as more likely to be genuine.")
    elif risk == "low" and n_rules == 1:
        s2 = ("This customer carries a low KYC risk rating and has no other corroborating rule "
              "hits, so the single trigger is more consistent with routine banking behavior "
              "producing a false positive than with a deliberate scheme, and the score is "
              "weighted down accordingly.")
    elif risk == "medium":
        s2 = ("This customer's medium KYC risk rating is treated as neutral context -- it "
              "neither strengthens nor weakens the case on its own, so it was not used to move "
              "the score in either direction, leaving the rule-hit pattern as the primary driver.")
    else:
        s2 = (f"This customer's {risk} KYC risk rating was reviewed as part of the contextual "
              f"assessment but did not, on its own, materially change the picture already "
              f"painted by the rule evidence.")

    if delta > 0:
        s3 = (f"Taken together, these factors supported nudging the score up by {delta} "
              f"point{'s' if delta != 1 else ''}, from {base} to {final} -- within the +/-10-point "
              f"band the copilot is permitted to apply -- because they increase the likelihood "
              f"this alert reflects genuine suspicious activity rather than routine behavior.")
    elif delta < 0:
        s3 = (f"Taken together, these factors supported nudging the score down by {abs(delta)} "
              f"point{'s' if abs(delta) != 1 else ''}, from {base} to {final} -- within the "
              f"+/-10-point band the copilot is permitted to apply -- because they reduce the "
              f"likelihood this alert reflects genuine suspicious activity rather than a benign "
              f"explanation.")
    else:
        s3 = (f"Taken together, these factors did not clear the bar for either an upward or "
              f"downward nudge, so the AI-adjusted score was left equal to the rule-based base "
              f"score of {base}, and any further calibration is deferred to the L1 analyst's "
              f"judgment.")

    return f"{s1} {s2} {s3}"


def mock_response(alert: dict) -> dict:
    """Deterministic stand-in for the LLM call, so the POC runs with no API key.
    Mirrors the exact JSON schema Claude is prompted to return."""
    base = alert["rule_based_score"]
    n_rules = len(alert["rules_triggered"])
    risk = alert["customer_risk_rating"]

    adjustment = 0
    if n_rules >= 2:
        adjustment += 8
    if risk == "high":
        adjustment += 5
    elif risk == "low" and n_rules == 1:
        adjustment -= 5
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
        "adjustment_reason": build_adjustment_reason(alert, base, final_score, n_rules, risk),
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
