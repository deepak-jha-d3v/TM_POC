"""
generate_data.py
-----------------
Creates a synthetic banking + crypto dataset (customers, accounts,
transactions, profile-update events) for the transaction-monitoring POC,
with known AML typologies deliberately injected across 12 rules so the
rules engine has real patterns to catch and you can verify detection
against ground truth.

No external dependencies beyond the standard library + pandas.
"""

import random
import uuid
from datetime import datetime, timedelta

import pandas as pd

random.seed(42)

OUT_DIR = "data"

FIRST_NAMES = ["James", "Maria", "Wei", "Fatima", "Carlos", "Olga", "Sam",
               "Priya", "Noah", "Elena", "Tariq", "Grace", "Diego", "Anya",
               "Liam", "Sofia", "Ahmed", "Ingrid", "Kenji", "Zainab",
               "Marcus", "Yuki", "Isabella", "Omar", "Freya", "Viktor",
               "Amara", "Hassan", "Lucia", "Dmitri", "Naomi", "Rafael",
               "Chidi", "Petra", "Youssef", "Mei", "Aleksander", "Camila",
               "Idris", "Nadia"]
LAST_NAMES = ["Smith", "Garcia", "Chen", "Khan", "Rossi", "Petrov", "Lee",
              "Patel", "Brown", "Novak", "Silva", "Kim", "Nguyen", "Cohen",
              "Adeyemi", "Muller", "Santos", "Ivanov", "Tanaka", "Osei",
              "Okafor", "Haddad", "Volkov", "Andersson", "Moreau", "Sato",
              "Reyes", "Kowalski", "Abara", "Farouk"]

LOW_RISK_COUNTRIES = ["US", "CA", "GB", "DE", "FR", "AU", "NL", "SG"]
HIGH_RISK_COUNTRIES = ["IR", "KP", "MM", "SY"]  # illustrative FATF-style watch list, POC only

OCCUPATIONS = ["Software Engineer", "Retail Manager", "Consultant", "Teacher",
               "Restaurant Owner", "Freelance Contractor", "Import/Export Trader",
               "Real Estate Agent", "Nurse", "Accountant", "Crypto Trader",
               "E-commerce Seller", "Marketing Manager", "Truck Driver"]

CRYPTO_SYMBOLS = ["BTC", "ETH", "USDT", "SOL", "XRP"]
EXCHANGE_NAMES = ["CoinBridge Exchange", "NovaCrypto", "OrbitalX", "ClearChain Exchange"]

N_CUSTOMERS = 90
START_DATE = datetime(2026, 4, 1)
END_DATE = datetime(2026, 6, 30)


def rand_date(start, end):
    delta = end - start
    return start + timedelta(seconds=random.randint(0, int(delta.total_seconds())))


def private_wallet_address():
    return "0x" + uuid.uuid4().hex[:40]


def buyer_id():
    return f"BUYER-{uuid.uuid4().hex[:6].upper()}"


def base_txn(account_id, customer, timestamp, amount, direction, channel,
             counterparty, counterparty_country, asset_type="FIAT",
             crypto_symbol="", wallet_type="", buyer=None, is_conversion=False):
    return {
        "txn_id": f"TXN{uuid.uuid4().hex[:10].upper()}",
        "account_id": account_id,
        "customer_id": customer["customer_id"],
        "timestamp": timestamp.isoformat() if hasattr(timestamp, "isoformat") else timestamp,
        "amount": round(amount, 2),
        "currency": "USD" if asset_type == "FIAT" else crypto_symbol,
        "direction": direction,
        "channel": channel,
        "counterparty": counterparty,
        "counterparty_country": counterparty_country,
        "asset_type": asset_type,
        "crypto_symbol": crypto_symbol,
        "wallet_type": wallet_type,
        "buyer_id": buyer or "",
        "is_conversion": is_conversion,
    }


def make_customers():
    customers = []
    for i in range(N_CUSTOMERS):
        cust_id = f"CUST{i+1:04d}"
        risk = random.choices(["low", "medium", "high"], weights=[0.55, 0.3, 0.15])[0]
        customers.append({
            "customer_id": cust_id,
            "name": f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}",
            "country": random.choice(LOW_RISK_COUNTRIES),
            "risk_rating": risk,
            "occupation": random.choice(OCCUPATIONS),
            "kyc_date": rand_date(datetime(2020, 1, 1), datetime(2025, 1, 1)).date().isoformat(),
            "expected_monthly_volume": random.choice([2000, 3500, 5000, 8000, 15000, 25000, 40000]),
            "uses_crypto": random.random() < 0.55,
        })
    return pd.DataFrame(customers)


def make_accounts(customers_df):
    accounts = []
    for _, cust in customers_df.iterrows():
        n_accounts = 1 if random.random() > 0.2 else 2
        for _ in range(n_accounts):
            accounts.append({
                "account_id": f"ACC{uuid.uuid4().hex[:8].upper()}",
                "customer_id": cust["customer_id"],
                "account_type": random.choice(["checking", "savings", "business"]),
                "open_date": rand_date(datetime(2019, 1, 1), datetime(2025, 6, 1)).date().isoformat(),
            })
    return pd.DataFrame(accounts)


def normal_transactions_for_account(account_id, customer, txns):
    """Baseline, unremarkable fiat activity for a customer over the window."""
    n = random.randint(15, 35)
    monthly_vol = customer["expected_monthly_volume"]
    for _ in range(n):
        amt = random.uniform(monthly_vol * 0.02, monthly_vol * 0.25)
        txns.append(base_txn(account_id, customer, rand_date(START_DATE, END_DATE), amt,
                              random.choice(["debit", "credit"]),
                              random.choice(["card", "ach", "wire"]),
                              f"Merchant/{random.choice(LAST_NAMES)}", customer["country"]))


def normal_crypto_activity(account_id, customer, txns, n=None):
    """Baseline, unremarkable crypto activity -- occasional buys/sells on an
    exchange wallet with a healthy spread of counterparties, used both as
    realistic noise and as the negative-control contrast for R04/R07/R10."""
    n = n or random.randint(3, 10)
    for _ in range(n):
        symbol = random.choice(CRYPTO_SYMBOLS)
        qty_value = random.uniform(200, 4000)
        direction = random.choice(["debit", "credit"])
        txns.append(base_txn(
            account_id, customer, rand_date(START_DATE, END_DATE), qty_value, direction,
            "crypto", random.choice(EXCHANGE_NAMES), customer["country"],
            asset_type="CRYPTO", crypto_symbol=symbol, wallet_type="exchange_wallet",
            buyer=(buyer_id() if direction == "credit" else None),
        ))


def sparse_transactions_for_account(account_id, customer, txns, n=3):
    """A handful of transactions early in the window, used as the baseline
    for accounts we're about to make look dormant-then-reactivated."""
    monthly_vol = customer["expected_monthly_volume"]
    for _ in range(n):
        amt = random.uniform(monthly_vol * 0.05, monthly_vol * 0.2)
        txns.append(base_txn(account_id, customer,
                              rand_date(START_DATE, START_DATE + timedelta(days=10)), amt,
                              random.choice(["debit", "credit"]), random.choice(["card", "ach"]),
                              f"Merchant/{random.choice(LAST_NAMES)}", customer["country"]))


# ---------- typology injectors (R01-R10 as specified, plus R11/R12 legacy/bonus) ----------

def inject_structuring(account_id, customer, txns, updates):
    """R01: multiple cash/ACH deposits just under the $10k CTR threshold, close together."""
    base_time = rand_date(START_DATE, END_DATE - timedelta(days=10))
    for i in range(4):
        txns.append(base_txn(account_id, customer, base_time + timedelta(hours=random.randint(0, 90)),
                              random.uniform(9000, 9900), "credit", random.choice(["cash", "ach"]),
                              "Cash deposit", customer["country"]))


def inject_profile_update_before_large_txn(account_id, customer, txns, updates):
    """R02: customer's contact/KYC details are changed shortly before a large,
    otherwise-unremarkable transaction -- a common precursor to account
    takeover or a mule account being repurposed."""
    txn_time = rand_date(START_DATE + timedelta(days=15), END_DATE - timedelta(days=5))
    update_time = txn_time - timedelta(hours=random.randint(6, 60))
    field = random.choice(["registered_address", "phone_number", "email", "beneficiary_details"])
    updates.append({
        "update_id": f"UPD{uuid.uuid4().hex[:8].upper()}",
        "customer_id": customer["customer_id"],
        "update_type": field,
        "update_timestamp": update_time.isoformat(),
    })
    txns.append(base_txn(account_id, customer, txn_time, random.uniform(22000, 55000),
                          "debit", "wire", "New Beneficiary Corp", customer["country"]))


def inject_unusual_spending_pattern(account_id, customer, txns, updates):
    """R03: one transaction far outside the customer's own historical range
    (statistical outlier vs. their normal spend), rather than a count-based
    velocity spike -- catches a single large anomalous movement."""
    monthly_vol = customer["expected_monthly_volume"]
    for _ in range(10):
        amt = random.uniform(monthly_vol * 0.02, monthly_vol * 0.12)
        txns.append(base_txn(account_id, customer, rand_date(START_DATE, END_DATE), amt,
                              random.choice(["debit", "credit"]), random.choice(["card", "ach"]),
                              f"Merchant/{random.choice(LAST_NAMES)}", customer["country"]))
    outlier_time = rand_date(START_DATE + timedelta(days=5), END_DATE - timedelta(days=5))
    txns.append(base_txn(account_id, customer, outlier_time, monthly_vol * random.uniform(3.5, 6),
                          "debit", "wire", "Outlier Counterparty LLC", customer["country"]))


def inject_low_buyer_diversity(account_id, customer, txns, updates):
    """R04: customer runs many crypto sales but is repeatedly paid by the same
    tiny handful of buyer wallets -- consistent with wash trading or a closed
    ring layering funds through 'legitimate-looking' peer-to-peer sales."""
    fixed_buyers = [buyer_id() for _ in range(2)]
    base_time = rand_date(START_DATE, END_DATE - timedelta(days=15))
    for i in range(9):
        symbol = random.choice(CRYPTO_SYMBOLS)
        txns.append(base_txn(
            account_id, customer, base_time + timedelta(days=i * 1.5), random.uniform(1500, 6000),
            "credit", "crypto", "P2P Marketplace", customer["country"],
            asset_type="CRYPTO", crypto_symbol=symbol, wallet_type="exchange_wallet",
            buyer=random.choice(fixed_buyers),
        ))


def inject_disproportionate_flow_through(account_id, customer, txns, updates):
    """R05: a large inbound transfer with a disproportionate share of it moved
    back out almost immediately -- the account behaves as a pass-through
    conduit rather than a genuine banking relationship."""
    t0 = rand_date(START_DATE, END_DATE - timedelta(days=3))
    amt = random.uniform(20000, 60000)
    txns.append(base_txn(account_id, customer, t0, amt, "credit", "wire",
                          "Pass-through Holdings LLC", customer["country"]))
    txns.append(base_txn(account_id, customer, t0 + timedelta(hours=random.randint(2, 40)),
                          amt * random.uniform(0.85, 0.97), "debit", "wire",
                          "Second Party Ventures Inc",
                          random.choice(LOW_RISK_COUNTRIES + HIGH_RISK_COUNTRIES)))


def inject_high_risk_countries(account_id, customer, txns, updates):
    """R06: wires or crypto-exchange counterparties tied to a watch-listed
    jurisdiction."""
    base_time = rand_date(START_DATE, END_DATE - timedelta(days=5))
    for i in range(2):
        if random.random() < 0.5:
            txns.append(base_txn(account_id, customer, base_time + timedelta(days=i * 2),
                                  random.uniform(15000, 40000), random.choice(["debit", "credit"]),
                                  "wire", f"Overseas Trading Co {i}", random.choice(HIGH_RISK_COUNTRIES)))
        else:
            symbol = random.choice(CRYPTO_SYMBOLS)
            txns.append(base_txn(account_id, customer, base_time + timedelta(days=i * 2),
                                  random.uniform(4000, 15000), "debit", "crypto",
                                  "Offshore Exchange Ltd", random.choice(HIGH_RISK_COUNTRIES),
                                  asset_type="CRYPTO", crypto_symbol=symbol, wallet_type="exchange_wallet"))


def inject_private_wallet_withdrawal(account_id, customer, txns, updates):
    """R07: crypto is acquired (fiat->crypto conversion) and then withdrawn
    almost immediately to an unhosted/private wallet -- removing funds from
    any custodial, monitorable environment shortly after entry."""
    t0 = rand_date(START_DATE, END_DATE - timedelta(days=5))
    symbol = random.choice(CRYPTO_SYMBOLS)
    amt = random.uniform(8000, 35000)
    txns.append(base_txn(account_id, customer, t0, amt, "credit", "crypto",
                          random.choice(EXCHANGE_NAMES), customer["country"],
                          asset_type="CRYPTO", crypto_symbol=symbol, wallet_type="exchange_wallet",
                          is_conversion=True))
    txns.append(base_txn(account_id, customer, t0 + timedelta(hours=random.uniform(0.5, 3)),
                          amt * random.uniform(0.9, 1.0), "debit", "crypto",
                          private_wallet_address(), customer["country"],
                          asset_type="CRYPTO", crypto_symbol=symbol, wallet_type="private_wallet"))


def inject_cash_transactions(account_id, customer, txns, updates):
    """R08: cash volume disproportionate to the customer's expected activity --
    a common front for placement of illicit funds."""
    base_time = rand_date(START_DATE, END_DATE - timedelta(days=20))
    for i in range(6):
        txns.append(base_txn(account_id, customer, base_time + timedelta(days=i * 3),
                              random.uniform(3000, 8500), "credit", "cash",
                              "Cash deposit", customer["country"]))


def inject_dormant_accounts(account_id, customer, txns, updates):
    """R09: a long stretch of no activity followed by a sudden burst -- often
    seen when a shell/mule account is "warmed up" then used briefly."""
    base_time = END_DATE - timedelta(days=4)
    for i in range(7):
        txns.append(base_txn(account_id, customer, base_time + timedelta(hours=i * 8),
                              random.uniform(2000, 9000), random.choice(["debit", "credit"]),
                              random.choice(["wire", "ach"]), f"New counterparty {i}", customer["country"]))


def inject_frequent_conversions(account_id, customer, txns, updates):
    """R10: rapid back-and-forth conversions between crypto and fiat -- a
    layering technique that obscures the trail by repeatedly hopping asset
    types rather than moving funds a large distance geographically."""
    base_time = rand_date(START_DATE, END_DATE - timedelta(days=20))
    for i in range(9):
        symbol = random.choice(CRYPTO_SYMBOLS)
        direction = "credit" if i % 2 == 0 else "debit"
        txns.append(base_txn(account_id, customer, base_time + timedelta(days=i * 2),
                              random.uniform(2000, 9000), direction, "crypto",
                              random.choice(EXCHANGE_NAMES), customer["country"],
                              asset_type="CRYPTO", crypto_symbol=symbol, wallet_type="exchange_wallet",
                              is_conversion=True))


def inject_round_tripping(account_id, customer, txns, updates):
    """R11 (legacy/bonus): funds sent out and a similar amount returns shortly
    after (circular fund flow)."""
    t0 = rand_date(START_DATE, END_DATE - timedelta(days=10))
    amt = random.uniform(12000, 30000)
    txns.append(base_txn(account_id, customer, t0, amt, "debit", "wire",
                          "Global Consulting Partners", customer["country"]))
    txns.append(base_txn(account_id, customer, t0 + timedelta(days=random.randint(3, 9)),
                          amt * random.uniform(0.9, 1.05), "credit", "wire",
                          "Global Consulting Partners Returns", customer["country"]))


def inject_multi_account_fragmentation(account_ids, customer, txns, updates):
    """R12 (legacy/bonus): near-threshold deposits deliberately spread across
    the customer's multiple accounts rather than concentrated in one
    (structuring variant)."""
    base_time = rand_date(START_DATE, END_DATE - timedelta(days=8))
    for i, acc_id in enumerate(account_ids):
        for j in range(2):
            txns.append(base_txn(acc_id, customer, base_time + timedelta(hours=(i * 20 + j * 30)),
                                  random.uniform(9000, 9800), "credit", random.choice(["cash", "ach"]),
                                  "Cash deposit", customer["country"]))


SINGLE_ACCOUNT_INJECTORS = [
    ("structuring", inject_structuring, 3),
    ("profile_update_before_large_txn", inject_profile_update_before_large_txn, 3),
    ("unusual_spending_pattern", inject_unusual_spending_pattern, 3),
    ("low_buyer_diversity", inject_low_buyer_diversity, 3),
    ("disproportionate_flow_through", inject_disproportionate_flow_through, 3),
    ("high_risk_countries", inject_high_risk_countries, 3),
    ("private_wallet_withdrawal", inject_private_wallet_withdrawal, 3),
    ("cash_transactions", inject_cash_transactions, 3),
    ("frequent_conversions", inject_frequent_conversions, 3),
    ("round_tripping", inject_round_tripping, 2),
]


def build_transactions(customers_df, accounts_df):
    all_txns = []
    profile_updates = []
    accounts_by_customer = accounts_df.groupby("customer_id")
    multi_account_customers = [cid for cid, grp in accounts_by_customer if len(grp) >= 2]
    single_account_customers = [cid for cid, grp in accounts_by_customer if len(grp) == 1]

    ground_truth = {}  # customer_id -> list of injected typologies

    dormant_pool = list(single_account_customers)
    random.shuffle(dormant_pool)
    dormant_customers = set(dormant_pool[:3])
    for cid in dormant_customers:
        ground_truth.setdefault(cid, []).append("dormant_accounts")

    pool = [c for c in customers_df["customer_id"] if c not in dormant_customers]
    random.shuffle(pool)
    it = iter(pool)

    additive_typologies = {}
    for name, fn, count in SINGLE_ACCOUNT_INJECTORS:
        for _ in range(count):
            cid = next(it)
            additive_typologies.setdefault(cid, []).append(fn)
            ground_truth.setdefault(cid, []).append(name)

    multi_acct_targets = []
    if multi_account_customers:
        available = [c for c in multi_account_customers
                     if c not in additive_typologies and c not in dormant_customers]
        random.shuffle(available)
        multi_acct_targets = available[:2]
        for cid in multi_acct_targets:
            ground_truth.setdefault(cid, []).append("multi_account_fragmentation")

    for _, customer in customers_df.iterrows():
        cust_id = customer["customer_id"]
        cust_accounts = accounts_by_customer.get_group(cust_id)
        primary_account = cust_accounts.iloc[0]["account_id"]
        uses_crypto = bool(customer["uses_crypto"])

        if cust_id in dormant_customers:
            sparse_transactions_for_account(primary_account, customer, all_txns)
            inject_dormant_accounts(primary_account, customer, all_txns, profile_updates)
            for _, acc in cust_accounts.iloc[1:].iterrows():
                normal_transactions_for_account(acc["account_id"], customer, all_txns)
            continue

        for _, acc in cust_accounts.iterrows():
            normal_transactions_for_account(acc["account_id"], customer, all_txns)
            if uses_crypto:
                normal_crypto_activity(acc["account_id"], customer, all_txns)

        if cust_id in multi_acct_targets:
            inject_multi_account_fragmentation(cust_accounts["account_id"].tolist(), customer,
                                                all_txns, profile_updates)
            continue

        for fn in additive_typologies.get(cust_id, []):
            fn(primary_account, customer, all_txns, profile_updates)

    df = pd.DataFrame(all_txns).sort_values("timestamp").reset_index(drop=True)
    updates_df = (pd.DataFrame(profile_updates).sort_values("update_timestamp").reset_index(drop=True)
                  if profile_updates else pd.DataFrame(
                      columns=["update_id", "customer_id", "update_type", "update_timestamp"]))
    return df, updates_df, ground_truth


if __name__ == "__main__":
    import os
    os.makedirs(OUT_DIR, exist_ok=True)

    customers_df = make_customers()
    accounts_df = make_accounts(customers_df)
    txns_df, updates_df, ground_truth = build_transactions(customers_df, accounts_df)

    customers_df.to_csv(f"{OUT_DIR}/customers.csv", index=False)
    accounts_df.to_csv(f"{OUT_DIR}/accounts.csv", index=False)
    txns_df.to_csv(f"{OUT_DIR}/transactions.csv", index=False)
    updates_df.to_csv(f"{OUT_DIR}/customer_profile_updates.csv", index=False)

    print(f"Customers:    {len(customers_df)}")
    print(f"Accounts:     {len(accounts_df)}")
    print(f"Transactions: {len(txns_df)}  (crypto: {(txns_df['asset_type']=='CRYPTO').sum()})")
    print(f"Profile updates: {len(updates_df)}")
    print(f"\nInjected typologies (ground truth, {len(ground_truth)} customers):")
    for cust_id, typologies in ground_truth.items():
        print(f"  {cust_id}: {', '.join(typologies)}")
