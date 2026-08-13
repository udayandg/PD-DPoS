"""Generate downgrade.csv - downgrade events for a random subset of blocks.

A random subset of the blocks in block_size.csv incur a downgrade event during
block production. Each event is one of two profiles:
    (slashing=0,  cooldown=0)  -> missed / invalid block, no slashing   (Step 9.3.2)
    (slashing=10, cooldown=5)  -> malicious double-publish, 10% slash    (Step 9.3.1)

Columns:
    Downgrade_id, Block_id, slashing, cooldown
"""
import csv
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
BLOCKSIZE_CSV = os.path.join(HERE, "block_size.csv")
OUT_CSV = os.path.join(HERE, "downgrade.csv")

DOWNGRADE_RATE = 0.05          # fraction of blocks that get a downgrade event
PROFILES = [(0, 0), (10, 5)]   # (slashing, cooldown)

random.seed(17)  # reproducible


def load_block_ids(path):
    with open(path, newline="") as f:
        return [row["Block_id"] for row in csv.DictReader(f)]


def main():
    block_ids = load_block_ids(BLOCKSIZE_CSV)
    n_events = round(len(block_ids) * DOWNGRADE_RATE)
    chosen = sorted(random.sample(block_ids, n_events))  # subset, keep block order

    with open(OUT_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Downgrade_id", "Block_id", "slashing", "cooldown"])
        for i, block_id in enumerate(chosen, start=1):
            slashing, cooldown = random.choice(PROFILES)
            w.writerow([i, block_id, slashing, cooldown])

    print(f"Wrote {n_events} downgrade events "
          f"({DOWNGRADE_RATE:.0%} of {len(block_ids)} blocks) -> {OUT_CSV}")


if __name__ == "__main__":
    main()
