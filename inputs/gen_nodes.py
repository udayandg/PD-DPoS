"""Generate 500 node records for PD-DPoS / DPoS simulation input.

Columns:
  account_id : node identifier (node_0001 .. node_0500)
  stake      : integer stake, 50 .. 10000
  processing : hash-rate/processing value, 0.00 .. 1.00 (2 decimals)
  k_shell    : k-shell connectivity value, 1 .. 4
"""
import csv
import os
import random

random.seed(42)  # reproducible dataset

N = 500
out_path = os.path.join(os.path.dirname(__file__), "nodes_500.csv")

with open(out_path, "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["account_id", "stake", "processing", "k_shell"])
    for i in range(1, N + 1):
        account_id = f"node_{i:04d}"
        stake = random.randint(50, 10000)
        processing = round(random.uniform(0.0, 1.0), 2)
        k_shell = random.randint(1, 4)
        writer.writerow([account_id, stake, f"{processing:.2f}", k_shell])

print(f"Wrote {N} nodes to {out_path}")


