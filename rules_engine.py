"""
rules_engine.py
----------------
Deterministic AML/transaction-monitoring rules. This is the "L1 detection"
layer: explainable, auditable, no black box. Each rule inspects a customer's
transaction history and returns a hit (or nothing) with a severity weight
and the evidence behind it. Hits are rolled up into an alert per customer.

This is intentionally simple (rule-of-thumb logic, not tuned thresholds) --
the point of the POC is to demonstrate the pipeline end to end, not to ship
production-grade AML scenarios.
"""

import json
from collections import defaultdict
from datetime import timedelta

import pandas as pd

HIGH_RISK_COUNTRIES = {"IR", "KP", "MM", "SY"}

CTR_THRESHOLD = 10000
STRUCTURING_LOW = 9000
STRUCTURING_WINDOW_DAYS = 5
STRUCTURING_MIN_COUNT = 3

RULE_WEIGHTS = {
    "R01_STRUCTURING": 35,
    "R02_VELOCITY": 20,
    "R03_HIGH_RISK_CORRIDOR": 30,
    "R04_RAPID_MOVEMENT": 30,
    "R05_ROUND_TRIPPING": 25,
    "R06_DORMANT_REACTIVATION": 15,
    "R07_CASH_INTENSIVE": 20,
    "R08_MULTI_ACCOUNT_FRAGMENTATION": 30,
}


def rule_structuring(cust_id, txns):
    deposits = txns[(txns["direction"] == "credit") &
                     (txns["amount"] >= STRUCTURING_LOW) &
                     (txns["amount"] < CTR_THRESHOLD)].sort_values("timestamp")
    if len(deposits) < STRUCTURING_MIN_COUNT:
        return None
    times = pd.to_datetime(deposits["timestamp"])
    window_start, window_end = times.min(), times.max()
    if (window_end - window_start) <= timedelta(days=STRUCTURING_WINDOW_DAYS):
        total = deposits["amount"].sum()
        return {
            "rule_id": "R01_STRUCTURING",
            "name": "Structuring (CTR avoidance)",
            "weight": RULE_WEIGHTS["R01_STRUCTURING"],
            "evidence": (f"{len(deposits)} deposits between ${STRUCTURING_LOW:,}-"
                         f"${CTR_THRESHOLD:,} totaling ${total:,.0f}, within "
                         f"{(window_end - window_start).days} days"),
        }
    return None


def rule_velocity(cust_id, txns, all_txns):
    """Flag if this customer's transaction count in any 48h window is far above
    their own overall average daily count."""
    if len(txns) < 5:
        return None
    times = sorted(pd.to_datetime(txns["timestamp"]).tolist())
    span_days = max((times[-1] - times[0]).days, 1)
    avg_per_2day = (len(txns) / span_days) * 2

    # find the busiest 48h window
    max_count = 0
    for t in times:
        window_count = sum(1 for x in times if t <= x <= t + timedelta(hours=48))
        max_count = max(max_count, window_count)

    if max_count >= 10 and max_count >= avg_per_2day * 4:
        return {
            "rule_id": "R02_VELOCITY",
            "name": "Velocity spike",
            "weight": RULE_WEIGHTS["R02_VELOCITY"],
            "evidence": (f"{max_count} transactions within a 48-hour window vs. "
                         f"an expected ~{avg_per_2day:.1f} for this customer"),
        }
    return None


def rule_high_risk_corridor(cust_id, txns):
    hits = txns[txns["counterparty_country"].isin(HIGH_RISK_COUNTRIES)]
    if len(hits) == 0:
        return None
    total = hits["amount"].sum()
    countries = sorted(hits["counterparty_country"].unique().tolist())
    return {
        "rule_id": "R03_HIGH_RISK_CORRIDOR",
        "name": "High-risk jurisdiction",
        "weight": RULE_WEIGHTS["R03_HIGH_RISK_CORRIDOR"],
        "evidence": (f"{len(hits)} transaction(s) totaling ${total:,.0f} involving "
                     f"watch-listed jurisdiction(s): {', '.join(countries)}"),
    }


def rule_rapid_movement(cust_id, txns):
    credits = txns[(txns["direction"] == "credit") & (txns["amount"] >= 10000)].sort_values("timestamp")
    debits = txns[(txns["direction"] == "debit")].sort_values("timestamp")
    for _, c in credits.iterrows():
        c_time = pd.to_datetime(c["timestamp"])
        window = debits[
            (pd.to_datetime(debits["timestamp"]) > c_time) &
            (pd.to_datetime(debits["timestamp"]) <= c_time + timedelta(hours=48))
        ]
        if len(window) == 0:
            continue
        out_total = window["amount"].sum()
        if out_total >= c["amount"] * 0.75:
            return {
                "rule_id": "R04_RAPID_MOVEMENT",
                "name": "Rapid movement of funds",
                "weight": RULE_WEIGHTS["R04_RAPID_MOVEMENT"],
                "evidence": (f"${c['amount']:,.0f} inbound on {c['timestamp'][:10]}, "
                             f"${out_total:,.0f} ({out_total/c['amount']*100:.0f}%) moved "
                             f"out within 48 hours"),
            }
    return None


def rule_round_tripping(cust_id, txns):
    """R05: money leaves and a similar amount comes back shortly after --
    a classic sign of layering / circular fund flow rather than genuine spend."""
    MIN_AMOUNT = 8000  # ignore everyday activity; only look at large transfers
    debits = txns[(txns["direction"] == "debit") & (txns["amount"] >= MIN_AMOUNT)].sort_values("timestamp")
    credits = txns[(txns["direction"] == "credit") & (txns["amount"] >= MIN_AMOUNT)]
    credit_times = pd.to_datetime(credits["timestamp"])
    for _, d in debits.iterrows():
        d_time = pd.to_datetime(d["timestamp"])
        window = credits[(credit_times > d_time) & (credit_times <= d_time + timedelta(days=10))]
        matches = window[(window["amount"] - d["amount"]).abs() <= d["amount"] * 0.1]
        if len(matches) > 0:
            best = matches.iloc[0]
            days = (pd.to_datetime(best["timestamp"]) - d_time).days
            return {
                "rule_id": "R05_ROUND_TRIPPING",
                "name": "Round-tripping",
                "weight": RULE_WEIGHTS["R05_ROUND_TRIPPING"],
                "evidence": (f"${d['amount']:,.0f} sent out on {d['timestamp'][:10]}, "
                             f"${best['amount']:,.0f} returned within {days} day(s) "
                             f"(circular fund flow)"),
            }
    return None


def rule_dormant_reactivation(cust_id, txns):
    """R06: a long stretch of no activity followed by a sudden burst --
    often seen when a shell/mule account is "warmed up" then used briefly."""
    times = sorted(pd.to_datetime(txns["timestamp"]).tolist())
    if len(times) < 5:
        return None
    gaps = [(times[i + 1] - times[i]).days for i in range(len(times) - 1)]
    max_gap = max(gaps)
    if max_gap < 45:
        return None
    idx = gaps.index(max_gap)
    reactivation_start = times[idx + 1]
    burst_count = sum(1 for t in times if reactivation_start <= t <= reactivation_start + timedelta(days=5))
    if burst_count >= 4:
        return {
            "rule_id": "R06_DORMANT_REACTIVATION",
            "name": "Dormant account reactivation",
            "weight": RULE_WEIGHTS["R06_DORMANT_REACTIVATION"],
            "evidence": (f"{max_gap}-day period of inactivity followed by {burst_count} "
                         f"transactions within 5 days of reactivation"),
        }
    return None


def rule_cash_intensive(cust_id, txns, expected_monthly_volume):
    """R07: cash volume disproportionate to the customer's expected activity level --
    a common front for placement of illicit funds."""
    cash_txns = txns[txns["channel"] == "cash"]
    total = txns["amount"].sum()
    if len(cash_txns) == 0 or total == 0:
        return None
    cash_total = cash_txns["amount"].sum()
    ratio = cash_total / total
    if ratio >= 0.45 and cash_total >= expected_monthly_volume * 1.2:
        return {
            "rule_id": "R07_CASH_INTENSIVE",
            "name": "Cash-intensive activity",
            "weight": RULE_WEIGHTS["R07_CASH_INTENSIVE"],
            "evidence": (f"${cash_total:,.0f} in cash transactions ({ratio*100:.0f}% of total "
                         f"activity), vs. expected monthly volume of ${expected_monthly_volume:,.0f}"),
        }
    return None


def rule_multi_account_fragmentation(cust_id, txns):
    """R08: near-CTR-threshold deposits deliberately spread across the customer's
    multiple accounts rather than concentrated in one -- structuring designed to
    evade single-account monitoring."""
    near_threshold = txns[(txns["direction"] == "credit") &
                           (txns["amount"] >= STRUCTURING_LOW) &
                           (txns["amount"] < CTR_THRESHOLD)]
    if len(near_threshold) < 3:
        return None
    per_account = near_threshold.groupby("account_id").size()
    accounts_with_hits = per_account[per_account >= 1]
    if len(accounts_with_hits) >= 2:
        total = near_threshold["amount"].sum()
        return {
            "rule_id": "R08_MULTI_ACCOUNT_FRAGMENTATION",
            "name": "Multi-account fragmentation",
            "weight": RULE_WEIGHTS["R08_MULTI_ACCOUNT_FRAGMENTATION"],
            "evidence": (f"{len(near_threshold)} near-threshold deposits totaling "
                         f"${total:,.0f}, split across {len(accounts_with_hits)} different accounts"),
        }
    return None


def run_rules(customers_df, transactions_df):
    """Returns a list of alert dicts, one per customer with >=1 rule hit."""
    alerts = []
    expected_volume_by_cust = customers_df.set_index("customer_id")["expected_monthly_volume"].to_dict()

    for cust_id, cust_txns in transactions_df.groupby("customer_id"):
        hits = []
        for fn in (rule_structuring, rule_high_risk_corridor, rule_rapid_movement,
                   rule_round_tripping, rule_dormant_reactivation, rule_multi_account_fragmentation):
            hit = fn(cust_id, cust_txns)
            if hit:
                hits.append(hit)
        vel_hit = rule_velocity(cust_id, cust_txns, transactions_df)
        if vel_hit:
            hits.append(vel_hit)
        cash_hit = rule_cash_intensive(cust_id, cust_txns, expected_volume_by_cust.get(cust_id, 5000))
        if cash_hit:
            hits.append(cash_hit)

        if hits:
            base_score = min(100, sum(h["weight"] for h in hits))
            customer_row = customers_df[customers_df["customer_id"] == cust_id].iloc[0]
            alerts.append({
                "alert_id": f"ALT-{cust_id}",
                "customer_id": cust_id,
                "customer_name": customer_row["name"],
                "customer_risk_rating": customer_row["risk_rating"],
                "customer_country": customer_row["country"],
                "rules_triggered": hits,
                "rule_based_score": base_score,
                "transaction_count_in_window": len(cust_txns),
            })
    alerts.sort(key=lambda a: a["rule_based_score"], reverse=True)
    return alerts


if __name__ == "__main__":
    import os
    os.makedirs("output", exist_ok=True)

    customers_df = pd.read_csv("data/customers.csv")
    transactions_df = pd.read_csv("data/transactions.csv")

    alerts = run_rules(customers_df, transactions_df)

    with open("output/alerts.json", "w") as f:
        json.dump(alerts, f, indent=2)

    print(f"Generated {len(alerts)} alert(s) from {len(transactions_df)} transactions "
          f"across {customers_df['customer_id'].nunique()} customers.\n")
    for a in alerts:
        rule_names = ", ".join(h["rule_id"] for h in a["rules_triggered"])
        print(f"  {a['alert_id']:<16} score={a['rule_based_score']:<4} rules=[{rule_names}]")
