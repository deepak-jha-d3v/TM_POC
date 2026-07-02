"""
rules_engine.py
----------------
Deterministic AML/transaction-monitoring rules. This is the "L1 detection"
layer: explainable, auditable, no black box. Each rule inspects a customer's
transaction history (and, where relevant, their profile-update history) and
returns a hit (or nothing) with a severity weight and concrete evidence.
Hits are rolled up into an alert per customer.

Rules R01-R10 are the ten rules requested for this build. R11-R12 are
carried over from the original POC (round-tripping, multi-account
fragmentation) and kept as bonus coverage since they were already
implemented and working.

This is intentionally simple (rule-of-thumb logic, not tuned thresholds) --
the point of the POC is to demonstrate the pipeline end to end, not to ship
production-grade AML scenarios.
"""

import json
from datetime import timedelta

import pandas as pd

HIGH_RISK_COUNTRIES = {"IR", "KP", "MM", "SY"}

CTR_THRESHOLD = 10000
STRUCTURING_LOW = 9000
STRUCTURING_WINDOW_DAYS = 5
STRUCTURING_MIN_COUNT = 3

RULE_WEIGHTS = {
    "R01_STRUCTURING": 35,
    "R02_PROFILE_UPDATE_BEFORE_LARGE_TXN": 20,
    "R03_UNUSUAL_SPENDING_PATTERN": 20,
    "R04_LOW_BUYER_DIVERSITY": 25,
    "R05_DISPROPORTIONATE_FLOW_THROUGH": 30,
    "R06_HIGH_RISK_COUNTRIES": 30,
    "R07_PRIVATE_WALLET_WITHDRAWAL": 30,
    "R08_CASH_TRANSACTIONS": 20,
    "R09_DORMANT_ACCOUNTS": 15,
    "R10_FREQUENT_CONVERSIONS": 25,
    "R11_ROUND_TRIPPING": 25,
    "R12_MULTI_ACCOUNT_FRAGMENTATION": 30,
}

RULE_NAMES = {
    "R01_STRUCTURING": "Detection of Structuring",
    "R02_PROFILE_UPDATE_BEFORE_LARGE_TXN": "Customer Details Updated Before a Large Transaction",
    "R03_UNUSUAL_SPENDING_PATTERN": "Unusual Spending Pattern",
    "R04_LOW_BUYER_DIVERSITY": "Low Buyers Diversity",
    "R05_DISPROPORTIONATE_FLOW_THROUGH": "Disproportionate Flow-Through",
    "R06_HIGH_RISK_COUNTRIES": "High-Risk Countries",
    "R07_PRIVATE_WALLET_WITHDRAWAL": "Immediate Withdrawal to Private Wallets",
    "R08_CASH_TRANSACTIONS": "Cash Transactions",
    "R09_DORMANT_ACCOUNTS": "Dormant Accounts",
    "R10_FREQUENT_CONVERSIONS": "Frequent Conversions Crypto-FIAT or FIAT-Crypto",
    "R11_ROUND_TRIPPING": "Round-Tripping (bonus)",
    "R12_MULTI_ACCOUNT_FRAGMENTATION": "Multi-Account Fragmentation (bonus)",
}


def _hit(rule_id, evidence):
    return {"rule_id": rule_id, "name": RULE_NAMES[rule_id], "weight": RULE_WEIGHTS[rule_id],
            "evidence": evidence}


# ---------- R01 Detection of Structuring ----------

def rule_structuring(cust_id, txns, updates_df):
    deposits = txns[(txns["direction"] == "credit") &
                     (txns["amount"] >= STRUCTURING_LOW) &
                     (txns["amount"] < CTR_THRESHOLD)].sort_values("timestamp")
    if len(deposits) < STRUCTURING_MIN_COUNT:
        return None
    times = pd.to_datetime(deposits["timestamp"], format='ISO8601')
    window_start, window_end = times.min(), times.max()
    if (window_end - window_start) <= timedelta(days=STRUCTURING_WINDOW_DAYS):
        total = deposits["amount"].sum()
        return _hit("R01_STRUCTURING",
                     f"{len(deposits)} deposits between ${STRUCTURING_LOW:,}-${CTR_THRESHOLD:,} "
                     f"totaling ${total:,.0f}, within {(window_end - window_start).days} days")
    return None


# ---------- R02 Customer Details Updated Before a Large Transaction ----------

def rule_profile_update_before_large_txn(cust_id, txns, updates_df):
    LARGE_TXN = 15000
    WINDOW_HOURS = 72
    if updates_df is None or updates_df.empty:
        return None
    cust_updates = updates_df[updates_df["customer_id"] == cust_id]
    if cust_updates.empty:
        return None
    large_txns = txns[txns["amount"] >= LARGE_TXN].sort_values("timestamp")
    if large_txns.empty:
        return None
    update_times = pd.to_datetime(cust_updates["update_timestamp"], format='ISO8601')
    for _, t in large_txns.iterrows():
        t_time = pd.to_datetime(t["timestamp"])
        preceding = cust_updates[(update_times <= t_time) &
                                  (update_times >= t_time - timedelta(hours=WINDOW_HOURS))]
        if len(preceding) > 0:
            upd = preceding.iloc[-1]
            hours_before = (t_time - pd.to_datetime(upd["update_timestamp"])).total_seconds() / 3600
            return _hit("R02_PROFILE_UPDATE_BEFORE_LARGE_TXN",
                         f"'{upd['update_type'].replace('_',' ')}' changed {hours_before:.0f}h before a "
                         f"${t['amount']:,.0f} transaction on {t['timestamp'][:10]}")
    return None


# ---------- R03 Unusual Spending Pattern ----------

def rule_unusual_spending_pattern(cust_id, txns, updates_df):
    if len(txns) < 8:
        return None
    amounts = txns["amount"]
    mean, std = amounts.mean(), amounts.std()
    if std == 0 or pd.isna(std):
        return None
    z = (amounts - mean) / std
    outliers = txns[z >= 3.5]
    if outliers.empty:
        return None
    worst = outliers.loc[amounts[outliers.index].idxmax()]
    z_score = (worst["amount"] - mean) / std
    return _hit("R03_UNUSUAL_SPENDING_PATTERN",
                f"${worst['amount']:,.0f} transaction on {worst['timestamp'][:10]} is "
                f"{z_score:.1f} standard deviations above this customer's own average of "
                f"${mean:,.0f}")


# ---------- R04 Low Buyers Diversity ----------

def rule_low_buyer_diversity(cust_id, txns, updates_df):
    MIN_SALES = 6
    MAX_DIVERSITY_RATIO = 0.35
    sales = txns[(txns["direction"] == "credit") & (txns.get("asset_type") == "CRYPTO") &
                 (txns["buyer_id"].astype(str) != "")]
    if len(sales) < MIN_SALES:
        return None
    unique_buyers = sales["buyer_id"].nunique()
    ratio = unique_buyers / len(sales)
    if ratio <= MAX_DIVERSITY_RATIO:
        total = sales["amount"].sum()
        return _hit("R04_LOW_BUYER_DIVERSITY",
                     f"{len(sales)} crypto sales totaling ${total:,.0f} concentrated across only "
                     f"{unique_buyers} distinct buyer wallet(s) ({ratio*100:.0f}% diversity ratio)")
    return None


# ---------- R05 Disproportionate Flow-Through ----------

def rule_disproportionate_flow_through(cust_id, txns, updates_df):
    credits = txns[(txns["direction"] == "credit") & (txns["amount"] >= 10000)].sort_values("timestamp")
    debits = txns[(txns["direction"] == "debit")].sort_values("timestamp")
    for _, c in credits.iterrows():
        c_time = pd.to_datetime(c["timestamp"])
        window = debits[(pd.to_datetime(debits["timestamp"], format='ISO8601') > c_time) &
                         (pd.to_datetime(debits["timestamp"], format='ISO8601') <= c_time + timedelta(hours=48))]
        if len(window) == 0:
            continue
        out_total = window["amount"].sum()
        if out_total >= c["amount"] * 0.75:
            return _hit("R05_DISPROPORTIONATE_FLOW_THROUGH",
                         f"${c['amount']:,.0f} inbound on {c['timestamp'][:10]}, ${out_total:,.0f} "
                         f"({out_total/c['amount']*100:.0f}%) moved back out within 48 hours")
    return None


# ---------- R06 High-Risk Countries ----------

def rule_high_risk_countries(cust_id, txns, updates_df):
    hits = txns[txns["counterparty_country"].isin(HIGH_RISK_COUNTRIES)]
    if len(hits) == 0:
        return None
    total = hits["amount"].sum()
    countries = sorted(hits["counterparty_country"].unique().tolist())
    crypto_hits = (hits.get("asset_type") == "CRYPTO").sum() if "asset_type" in hits else 0
    channel_note = f", including {crypto_hits} crypto-exchange transaction(s)" if crypto_hits else ""
    return _hit("R06_HIGH_RISK_COUNTRIES",
                f"{len(hits)} transaction(s) totaling ${total:,.0f} involving watch-listed "
                f"jurisdiction(s): {', '.join(countries)}{channel_note}")


# ---------- R07 Immediate Withdrawal to Private Wallets ----------

def rule_private_wallet_withdrawal(cust_id, txns, updates_df):
    WINDOW_HOURS = 6
    crypto_in = txns[(txns["direction"] == "credit") & (txns.get("asset_type") == "CRYPTO")].sort_values("timestamp")
    private_out = txns[(txns["direction"] == "debit") & (txns.get("wallet_type") == "private_wallet")].sort_values("timestamp")
    if crypto_in.empty or private_out.empty:
        return None
    private_times = pd.to_datetime(private_out["timestamp"])
    for _, c in crypto_in.iterrows():
        c_time = pd.to_datetime(c["timestamp"])
        window = private_out[(private_times > c_time) & (private_times <= c_time + timedelta(hours=WINDOW_HOURS))]
        if window.empty:
            continue
        out_total = window["amount"].sum()
        if out_total >= c["amount"] * 0.85:
            hrs = (pd.to_datetime(window.iloc[0]["timestamp"]) - c_time).total_seconds() / 3600
            return _hit("R07_PRIVATE_WALLET_WITHDRAWAL",
                        f"${c['amount']:,.0f} of {c.get('crypto_symbol','crypto')} credited on "
                        f"{c['timestamp'][:10]}, {out_total/c['amount']*100:.0f}% withdrawn to an "
                        f"unhosted private wallet within {hrs:.1f}h")
    return None


# ---------- R08 Cash Transactions ----------

def rule_cash_transactions(cust_id, txns, updates_df, expected_monthly_volume):
    cash_txns = txns[txns["channel"] == "cash"]
    total = txns["amount"].sum()
    if len(cash_txns) == 0 or total == 0:
        return None
    cash_total = cash_txns["amount"].sum()
    ratio = cash_total / total
    if ratio >= 0.45 and cash_total >= expected_monthly_volume * 1.2:
        return _hit("R08_CASH_TRANSACTIONS",
                     f"${cash_total:,.0f} in cash transactions ({ratio*100:.0f}% of total activity), "
                     f"vs. expected monthly volume of ${expected_monthly_volume:,.0f}")
    return None


# ---------- R09 Dormant Accounts ----------

def rule_dormant_accounts(cust_id, txns, updates_df):
    times = sorted(pd.to_datetime(txns["timestamp"], format='ISO8601').tolist())
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
        return _hit("R09_DORMANT_ACCOUNTS",
                     f"{max_gap}-day period of inactivity followed by {burst_count} transactions "
                     f"within 5 days of reactivation")
    return None


# ---------- R10 Frequent Conversions Crypto-FIAT or FIAT-Crypto ----------

def rule_frequent_conversions(cust_id, txns, updates_df):
    WINDOW_DAYS = 30
    MIN_COUNT = 6
    conversions = txns[txns.get("is_conversion") == True].sort_values("timestamp")  # noqa: E712
    if len(conversions) < MIN_COUNT:
        return None
    times = pd.to_datetime(conversions["timestamp"], format='ISO8601').tolist()
    max_count = 0
    window_end_idx = 0
    for i, t in enumerate(times):
        count = sum(1 for x in times if t <= x <= t + timedelta(days=WINDOW_DAYS))
        if count > max_count:
            max_count = count
    if max_count >= MIN_COUNT:
        total = conversions["amount"].sum()
        symbols = sorted(conversions["crypto_symbol"].dropna().unique().tolist())
        return _hit("R10_FREQUENT_CONVERSIONS",
                     f"{max_count} crypto<->fiat conversions ({', '.join(symbols)}) totaling "
                     f"${total:,.0f} within a {WINDOW_DAYS}-day window")
    return None


# ---------- R11 Round-Tripping (bonus/legacy) ----------

def rule_round_tripping(cust_id, txns, updates_df):
    MIN_AMOUNT = 8000
    debits = txns[(txns["direction"] == "debit") & (txns["amount"] >= MIN_AMOUNT)].sort_values("timestamp")
    credits = txns[(txns["direction"] == "credit") & (txns["amount"] >= MIN_AMOUNT)]
    credit_times = pd.to_datetime(credits["timestamp"], format='ISO8601')
    for _, d in debits.iterrows():
        d_time = pd.to_datetime(d["timestamp"])
        window = credits[(credit_times > d_time) & (credit_times <= d_time + timedelta(days=10))]
        matches = window[(window["amount"] - d["amount"]).abs() <= d["amount"] * 0.1]
        if len(matches) > 0:
            best = matches.iloc[0]
            days = (pd.to_datetime(best["timestamp"]) - d_time).days
            return _hit("R11_ROUND_TRIPPING",
                         f"${d['amount']:,.0f} sent out on {d['timestamp'][:10]}, ${best['amount']:,.0f} "
                         f"returned within {days} day(s) (circular fund flow)")
    return None


# ---------- R12 Multi-Account Fragmentation (bonus/legacy) ----------

def rule_multi_account_fragmentation(cust_id, txns, updates_df):
    near_threshold = txns[(txns["direction"] == "credit") &
                           (txns["amount"] >= STRUCTURING_LOW) &
                           (txns["amount"] < CTR_THRESHOLD)]
    if len(near_threshold) < 3:
        return None
    per_account = near_threshold.groupby("account_id").size()
    accounts_with_hits = per_account[per_account >= 1]
    if len(accounts_with_hits) >= 2:
        total = near_threshold["amount"].sum()
        return _hit("R12_MULTI_ACCOUNT_FRAGMENTATION",
                     f"{len(near_threshold)} near-threshold deposits totaling ${total:,.0f}, split "
                     f"across {len(accounts_with_hits)} different accounts")
    return None


SIMPLE_RULES = (
    rule_structuring,
    rule_profile_update_before_large_txn,
    rule_unusual_spending_pattern,
    rule_low_buyer_diversity,
    rule_disproportionate_flow_through,
    rule_high_risk_countries,
    rule_private_wallet_withdrawal,
    rule_dormant_accounts,
    rule_frequent_conversions,
    rule_round_tripping,
    rule_multi_account_fragmentation,
)


def run_rules(customers_df, transactions_df, updates_df=None):
    """Returns a list of alert dicts, one per customer with >=1 rule hit."""
    alerts = []
    expected_volume_by_cust = customers_df.set_index("customer_id")["expected_monthly_volume"].to_dict()

    for cust_id, cust_txns in transactions_df.groupby("customer_id"):
        hits = []
        for fn in SIMPLE_RULES:
            hit = fn(cust_id, cust_txns, updates_df)
            if hit:
                hits.append(hit)
        cash_hit = rule_cash_transactions(cust_id, cust_txns, updates_df,
                                           expected_volume_by_cust.get(cust_id, 5000))
        if cash_hit:
            hits.append(cash_hit)

        if hits:
            base_score = min(100, sum(h["weight"] for h in hits))
            customer_row = customers_df[customers_df["customer_id"] == cust_id].iloc[0]
            crypto_txn_count = int((cust_txns.get("asset_type") == "CRYPTO").sum())
            alerts.append({
                "alert_id": f"ALT-{cust_id}",
                "customer_id": cust_id,
                "customer_name": customer_row["name"],
                "customer_risk_rating": customer_row["risk_rating"],
                "customer_country": customer_row["country"],
                "rules_triggered": hits,
                "rule_based_score": base_score,
                "transaction_count_in_window": len(cust_txns),
                "crypto_transaction_count": crypto_txn_count,
            })
    alerts.sort(key=lambda a: a["rule_based_score"], reverse=True)
    return alerts


if __name__ == "__main__":
    import os
    os.makedirs("output", exist_ok=True)

    customers_df = pd.read_csv("data/customers.csv")
    transactions_df = pd.read_csv("data/transactions.csv")
    try:
        updates_df = pd.read_csv("data/customer_profile_updates.csv")
    except FileNotFoundError:
        updates_df = pd.DataFrame(columns=["update_id", "customer_id", "update_type", "update_timestamp"])

    alerts = run_rules(customers_df, transactions_df, updates_df)

    with open("output/alerts.json", "w") as f:
        json.dump(alerts, f, indent=2)

    print(f"Generated {len(alerts)} alert(s) from {len(transactions_df)} transactions "
          f"across {customers_df['customer_id'].nunique()} customers.\n")
    for a in alerts:
        rule_names = ", ".join(h["rule_id"] for h in a["rules_triggered"])
        print(f"  {a['alert_id']:<16} score={a['rule_based_score']:<4} rules=[{rule_names}]")
