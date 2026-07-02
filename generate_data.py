"""
generate_data.py
-----------------
Creates a synthetic banking dataset (customers, accounts, transactions) for
the transaction-monitoring POC, with known AML typologies deliberately
injected so the rules engine has real patterns to catch and you can verify
detection against ground truth.

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
               "Liam", "Sofia", "Ahmed", "Ingrid", "Kenji", "Zainab"]
LAST_NAMES = ["Smith", "Garcia", "Chen", "Khan", "Rossi", "Petrov", "Lee",
              "Patel", "Brown", "Novak", "Silva", "Kim", "Nguyen", "Cohen",
              "Adeyemi", "Muller", "Santos", "Ivanov", "Tanaka", "Osei"]

LOW_RISK_COUNTRIES = ["US", "CA", "GB", "DE", "FR", "AU"]
HIGH_RISK_COUNTRIES = ["IR", "KP", "MM", "SY"]  # illustrative FATF-style watch list, POC only

OCCUPATIONS = ["Software Engineer", "Retail Manager", "Consultant", "Teacher",
               "Restaurant Owner", "Freelance Contractor", "Import/Export Trader",
               "Real Estate Agent", "Nurse", "Accountant"]

N_CUSTOMERS = 60
START_DATE = datetime(2026, 4, 1)
END_DATE = datetime(2026, 6, 30)


def rand_date(start, end):
    delta = end - start
    return start + timedelta(seconds=random.randint(0, int(delta.total_seconds())))


def make_customers():
    customers = []
    for i in range(N_CUSTOMERS):
        cust_id = f"CUST{i+1:04d}"
        risk = random.choices(["low", "medium", "high"], weights=[0.6, 0.3, 0.1])[0]
        customers.append({
            "customer_id": cust_id,
            "name": f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}",
            "country": random.choice(LOW_RISK_COUNTRIES),
            "risk_rating": risk,
            "occupation": random.choice(OCCUPATIONS),
            "kyc_date": rand_date(datetime(2020, 1, 1), datetime(2025, 1, 1)).date().isoformat(),
            "expected_monthly_volume": random.choice([2000, 3500, 5000, 8000, 15000, 25000]),
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


def normal_transactions_for_account(account_id, customer):
    """Baseline, unremarkable activity for a customer over the window."""
    txns = []
    n = random.randint(15, 35)
    monthly_vol = customer["expected_monthly_volume"]
    for _ in range(n):
        amt = round(random.uniform(monthly_vol * 0.02, monthly_vol * 0.25), 2)
        txns.append({
            "txn_id": f"TXN{uuid.uuid4().hex[:10].upper()}",
            "account_id": account_id,
            "customer_id": customer["customer_id"],
            "timestamp": rand_date(START_DATE, END_DATE).isoformat(),
            "amount": amt,
            "currency": "USD",
            "direction": random.choice(["debit", "credit"]),
            "channel": random.choice(["card", "ach", "wire"]),
            "counterparty": f"Merchant/{random.choice(LAST_NAMES)}",
            "counterparty_country": customer["country"],
        })
    return txns


def sparse_transactions_for_account(account_id, customer, n=3):
    """A handful of transactions early in the window, used as the baseline
    for accounts we're about to make look dormant-then-reactivated."""
    txns = []
    monthly_vol = customer["expected_monthly_volume"]
    for _ in range(n):
        amt = round(random.uniform(monthly_vol * 0.05, monthly_vol * 0.2), 2)
        txns.append({
            "txn_id": f"TXN{uuid.uuid4().hex[:10].upper()}",
            "account_id": account_id,
            "customer_id": customer["customer_id"],
            "timestamp": rand_date(START_DATE, START_DATE + timedelta(days=10)).isoformat(),
            "amount": amt,
            "currency": "USD",
            "direction": random.choice(["debit", "credit"]),
            "channel": random.choice(["card", "ach"]),
            "counterparty": f"Merchant/{random.choice(LAST_NAMES)}",
            "counterparty_country": customer["country"],
        })
    return txns


# ---------- typology injectors ----------

def inject_structuring(account_id, customer, txns):
    """R01: multiple cash/ACH deposits just under the $10k CTR threshold, close together."""
    base_time = rand_date(START_DATE, END_DATE - timedelta(days=10))
    for i in range(4):
        txns.append({
            "txn_id": f"TXN{uuid.uuid4().hex[:10].upper()}",
            "account_id": account_id, "customer_id": customer["customer_id"],
            "timestamp": (base_time + timedelta(hours=random.randint(0, 90))).isoformat(),
            "amount": round(random.uniform(9000, 9900), 2), "currency": "USD",
            "direction": "credit", "channel": random.choice(["cash", "ach"]),
            "counterparty": "Cash deposit", "counterparty_country": customer["country"],
        })


def inject_high_risk_corridor(account_id, customer, txns):
    """R03: wires to/from a high-risk jurisdiction."""
    base_time = rand_date(START_DATE, END_DATE - timedelta(days=5))
    for i in range(2):
        txns.append({
            "txn_id": f"TXN{uuid.uuid4().hex[:10].upper()}",
            "account_id": account_id, "customer_id": customer["customer_id"],
            "timestamp": (base_time + timedelta(days=i * 2)).isoformat(),
            "amount": round(random.uniform(15000, 40000), 2), "currency": "USD",
            "direction": random.choice(["debit", "credit"]), "channel": "wire",
            "counterparty": f"Overseas Trading Co {i}",
            "counterparty_country": random.choice(HIGH_RISK_COUNTRIES),
        })


def inject_rapid_movement(account_id, customer, txns):
    """R04: large inbound wire followed almost immediately by near-total outbound transfer."""
    t0 = rand_date(START_DATE, END_DATE - timedelta(days=3))
    amt = round(random.uniform(20000, 60000), 2)
    txns.append({
        "txn_id": f"TXN{uuid.uuid4().hex[:10].upper()}",
        "account_id": account_id, "customer_id": customer["customer_id"],
        "timestamp": t0.isoformat(), "amount": amt, "currency": "USD",
        "direction": "credit", "channel": "wire",
        "counterparty": "Pass-through Holdings LLC", "counterparty_country": customer["country"],
    })
    txns.append({
        "txn_id": f"TXN{uuid.uuid4().hex[:10].upper()}",
        "account_id": account_id, "customer_id": customer["customer_id"],
        "timestamp": (t0 + timedelta(hours=random.randint(2, 40))).isoformat(),
        "amount": round(amt * random.uniform(0.85, 0.97), 2), "currency": "USD",
        "direction": "debit", "channel": "wire",
        "counterparty": "Second Party Ventures Inc",
        "counterparty_country": random.choice(LOW_RISK_COUNTRIES + HIGH_RISK_COUNTRIES),
    })


def inject_velocity_spike(account_id, customer, txns):
    """R02: a short burst of many transactions far above normal cadence."""
    base_time = rand_date(START_DATE, END_DATE - timedelta(days=2))
    for i in range(12):
        txns.append({
            "txn_id": f"TXN{uuid.uuid4().hex[:10].upper()}",
            "account_id": account_id, "customer_id": customer["customer_id"],
            "timestamp": (base_time + timedelta(hours=i * 1.5)).isoformat(),
            "amount": round(random.uniform(800, 2500), 2), "currency": "USD",
            "direction": random.choice(["debit", "credit"]), "channel": "card",
            "counterparty": f"POS Terminal {i}", "counterparty_country": customer["country"],
        })


def inject_round_tripping(account_id, customer, txns):
    """R05: funds sent out and a similar amount returns shortly after (circular flow)."""
    t0 = rand_date(START_DATE, END_DATE - timedelta(days=10))
    amt = round(random.uniform(12000, 30000), 2)
    txns.append({
        "txn_id": f"TXN{uuid.uuid4().hex[:10].upper()}",
        "account_id": account_id, "customer_id": customer["customer_id"],
        "timestamp": t0.isoformat(), "amount": amt, "currency": "USD",
        "direction": "debit", "channel": "wire",
        "counterparty": "Global Consulting Partners", "counterparty_country": customer["country"],
    })
    txns.append({
        "txn_id": f"TXN{uuid.uuid4().hex[:10].upper()}",
        "account_id": account_id, "customer_id": customer["customer_id"],
        "timestamp": (t0 + timedelta(days=random.randint(3, 9))).isoformat(),
        "amount": round(amt * random.uniform(0.9, 1.05), 2), "currency": "USD",
        "direction": "credit", "channel": "wire",
        "counterparty": "Global Consulting Partners Returns",
        "counterparty_country": customer["country"],
    })


def inject_cash_intensive(account_id, customer, txns):
    """R07: cash volume disproportionate to the customer's expected activity."""
    base_time = rand_date(START_DATE, END_DATE - timedelta(days=20))
    for i in range(6):
        txns.append({
            "txn_id": f"TXN{uuid.uuid4().hex[:10].upper()}",
            "account_id": account_id, "customer_id": customer["customer_id"],
            "timestamp": (base_time + timedelta(days=i * 3)).isoformat(),
            "amount": round(random.uniform(3000, 8500), 2), "currency": "USD",
            "direction": "credit", "channel": "cash",
            "counterparty": "Cash deposit", "counterparty_country": customer["country"],
        })


def inject_dormant_burst(account_id, customer, txns):
    """R06: sudden burst of activity after a long stretch of inactivity."""
    base_time = END_DATE - timedelta(days=4)
    for i in range(7):
        txns.append({
            "txn_id": f"TXN{uuid.uuid4().hex[:10].upper()}",
            "account_id": account_id, "customer_id": customer["customer_id"],
            "timestamp": (base_time + timedelta(hours=i * 8)).isoformat(),
            "amount": round(random.uniform(2000, 9000), 2), "currency": "USD",
            "direction": random.choice(["debit", "credit"]), "channel": random.choice(["wire", "ach"]),
            "counterparty": f"New counterparty {i}", "counterparty_country": customer["country"],
        })


def inject_multi_account_fragmentation(account_ids, customer, txns):
    """R08: near-threshold deposits deliberately spread across the customer's
    multiple accounts rather than concentrated in one (structuring variant)."""
    base_time = rand_date(START_DATE, END_DATE - timedelta(days=8))
    for i, acc_id in enumerate(account_ids):
        for j in range(2):
            txns.append({
                "txn_id": f"TXN{uuid.uuid4().hex[:10].upper()}",
                "account_id": acc_id, "customer_id": customer["customer_id"],
                "timestamp": (base_time + timedelta(hours=(i * 20 + j * 30))).isoformat(),
                "amount": round(random.uniform(9000, 9800), 2), "currency": "USD",
                "direction": "credit", "channel": random.choice(["cash", "ach"]),
                "counterparty": "Cash deposit", "counterparty_country": customer["country"],
            })


def build_transactions(customers_df, accounts_df):
    all_txns = []
    accounts_by_customer = accounts_df.groupby("customer_id")
    multi_account_customers = [cid for cid, grp in accounts_by_customer if len(grp) >= 2]

    additive_typologies = {}  # customer_id -> injector fn (applied on top of baseline)
    dormant_customers = set()  # customer_id -> gets sparse baseline + burst instead of normal
    multi_acct_target = None

    single_account_customers = [cid for cid, grp in accounts_by_customer if len(grp) == 1]
    dormant_pool = [c for c in single_account_customers]
    random.shuffle(dormant_pool)
    for _ in range(2):
        cust_id = dormant_pool.pop()
        dormant_customers.add(cust_id)

    pool = [c for c in customers_df["customer_id"] if c not in dormant_customers]
    random.shuffle(pool)
    it = iter(pool)

    for _ in range(2):
        additive_typologies[next(it)] = inject_structuring
    for _ in range(2):
        additive_typologies[next(it)] = inject_high_risk_corridor
    for _ in range(2):
        additive_typologies[next(it)] = inject_rapid_movement
    additive_typologies[next(it)] = inject_velocity_spike
    for _ in range(2):
        additive_typologies[next(it)] = inject_round_tripping
    for _ in range(2):
        additive_typologies[next(it)] = inject_cash_intensive

    if multi_account_customers:
        available = [c for c in multi_account_customers
                     if c not in additive_typologies and c not in dormant_customers]
        if available:
            multi_acct_target = random.choice(available)

    for _, customer in customers_df.iterrows():
        cust_id = customer["customer_id"]
        cust_accounts = accounts_by_customer.get_group(cust_id)
        primary_account = cust_accounts.iloc[0]["account_id"]

        if cust_id in dormant_customers:
            all_txns.extend(sparse_transactions_for_account(primary_account, customer))
            inject_dormant_burst(primary_account, customer, all_txns)
            for _, acc in cust_accounts.iloc[1:].iterrows():
                all_txns.extend(normal_transactions_for_account(acc["account_id"], customer))
            continue

        for _, acc in cust_accounts.iterrows():
            all_txns.extend(normal_transactions_for_account(acc["account_id"], customer))

        if cust_id == multi_acct_target:
            inject_multi_account_fragmentation(cust_accounts["account_id"].tolist(), customer, all_txns)
            continue

        inject_fn = additive_typologies.get(cust_id)
        if inject_fn:
            inject_fn(primary_account, customer, all_txns)

    ground_truth = {cid: fn.__name__.replace("inject_", "") for cid, fn in additive_typologies.items()}
    for cid in dormant_customers:
        ground_truth[cid] = "dormant_reactivation"
    if multi_acct_target:
        ground_truth[multi_acct_target] = "multi_account_fragmentation"

    df = pd.DataFrame(all_txns).sort_values("timestamp").reset_index(drop=True)
    return df, ground_truth


if __name__ == "__main__":
    import os
    os.makedirs(OUT_DIR, exist_ok=True)

    customers_df = make_customers()
    accounts_df = make_accounts(customers_df)
    txns_df, ground_truth = build_transactions(customers_df, accounts_df)

    customers_df.to_csv(f"{OUT_DIR}/customers.csv", index=False)
    accounts_df.to_csv(f"{OUT_DIR}/accounts.csv", index=False)
    txns_df.to_csv(f"{OUT_DIR}/transactions.csv", index=False)

    print(f"Customers:    {len(customers_df)}")
    print(f"Accounts:     {len(accounts_df)}")
    print(f"Transactions: {len(txns_df)}")
    print(f"\nInjected typologies (ground truth, {len(ground_truth)} customers):")
    for cust_id, typology in ground_truth.items():
        print(f"  {cust_id}: {typology}")
