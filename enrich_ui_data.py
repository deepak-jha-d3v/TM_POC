"""
enrich_ui_data.py
------------------
Attaches a short list of representative "supporting transactions" to each
alert (large amounts, crypto activity, private-wallet withdrawals, high-risk
corridors) so the case-review UI can show concrete evidence rows under each
accordion item instead of evidence text alone. Reads output/alerts_with_ai.json
+ data/transactions.csv, writes output/alerts_for_ui.json.
"""

import json

import pandas as pd

INTERESTING_COLS = ["timestamp", "amount", "currency", "direction", "channel",
                     "counterparty", "counterparty_country", "asset_type",
                     "crypto_symbol", "wallet_type", "buyer_id", "is_conversion"]


def pick_sample_transactions(cust_txns, max_n=8):
    df = cust_txns.copy()
    df["_interest"] = 0.0
    df.loc[df["amount"] >= 10000, "_interest"] += 3
    df.loc[df.get("wallet_type") == "private_wallet", "_interest"] += 4
    df.loc[df.get("asset_type") == "CRYPTO", "_interest"] += 2
    df.loc[df["channel"] == "cash", "_interest"] += 2
    df.loc[df.get("is_conversion") == True, "_interest"] += 2  # noqa: E712
    df.loc[df["counterparty_country"].isin(["IR", "KP", "MM", "SY"]), "_interest"] += 3
    df["_interest"] += df["amount"] / df["amount"].max() if df["amount"].max() else 0
    top = df.sort_values("_interest", ascending=False).head(max_n)
    top = top.sort_values("timestamp")
    records = []
    for _, r in top.iterrows():
        rec = {c: (r[c] if c in r and pd.notna(r[c]) else "") for c in INTERESTING_COLS}
        rec["timestamp"] = str(rec["timestamp"])[:19].replace("T", " ")
        rec["is_conversion"] = bool(rec["is_conversion"]) if rec["is_conversion"] != "" else False
        rec["amount"] = float(rec["amount"])
        records.append(rec)
    return records


if __name__ == "__main__":
    with open("output/alerts_with_ai.json") as f:
        alerts = json.load(f)

    transactions_df = pd.read_csv("data/transactions.csv")

    for alert in alerts:
        cust_txns = transactions_df[transactions_df["customer_id"] == alert["customer_id"]]
        alert["sample_transactions"] = pick_sample_transactions(cust_txns)

    with open("output/alerts_for_ui.json", "w") as f:
        json.dump(alerts, f, indent=2)

    print(f"Enriched {len(alerts)} alerts with sample transactions -> output/alerts_for_ui.json")
