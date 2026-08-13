"""Generate mem_pool.csv - the transaction memory pool (Algorithm Step 9.1).

A flat pool of 500,000 transactions. Each transaction transfers a stake amount
in [1, 50] from one account_id to a different account_id (accounts taken from
nodes_500.csv), and carries a fixed transaction fee Tx_fee = 1.

One row per transaction:
    tx_id, from_account, to_account, amount, Tx_fee
(tx_id is a global sequential id starting at 1.)
"""
import csv
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
NODES_CSV = os.path.join(HERE, "nodes_500.csv")
OUT_CSV = os.path.join(HERE, "mem_pool.csv")

N_TX = 500000
AMT_MIN, AMT_MAX = 1, 50
TX_FEE = 1

random.seed(11)  # reproducible


def load_accounts(path):
    with open(path, newline="") as f:
        return [row["account_id"] for row in csv.DictReader(f)]


def main():
    accounts = load_accounts(NODES_CSV)
    total_stake = 0
    with open(OUT_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["tx_id", "from_account", "to_account", "amount", "Tx_fee"])
        for i in range(1, N_TX + 1):
            sender = random.choice(accounts)
            receiver = random.choice(accounts)
            while receiver == sender:            # no self-transfer
                receiver = random.choice(accounts)
            amount = random.randint(AMT_MIN, AMT_MAX)
            w.writerow([i, sender, receiver, amount, TX_FEE])
            total_stake += amount
    print(f"Wrote {N_TX} transactions "
          f"({total_stake} total stake transferred, {N_TX * TX_FEE} total fees) "
          f"-> {OUT_CSV}")


if __name__ == "__main__":
    main()
