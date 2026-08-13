"""Generate block_size.csv - block transaction-count plan for the mem pool.

Partitions the 500,000 transactions of mem_pool.csv into blocks whose sizes are
random in [400, 650] and sum to EXACTLY 500,000. Sizes are shuffled so the
randomness is spread evenly across all blocks (no clustering at either end).
Each block maps to a contiguous slice of mem_pool tx_ids (noted in remarks).

Columns:
    Block_id, transaction_quantity, remarks
"""
import csv
import math
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_CSV = os.path.join(HERE, "block_size.csv")

TOTAL_TX = 500000
SIZE_MIN, SIZE_MAX = 400, 650

random.seed(13)  # reproducible


def plan_sizes(total, lo, hi):
    """Return a list of block sizes in [lo, hi] summing exactly to total.

    Feasible block count n satisfies n*lo <= total <= n*hi. We pick a random
    feasible n, give every block the base size `lo`, then hand out the leftover
    (total - n*lo) as per-block extras in [0, hi-lo] using a running guard so the
    remaining extra always stays distributable. Finally the sizes are shuffled
    so any residual ordering bias from the guard is removed.
    """
    n_min = math.ceil(total / hi)
    n_max = total // lo
    n = random.randint(n_min, n_max)

    span = hi - lo
    remaining_extra = total - n * lo          # in [0, span*n]
    sizes = []
    for i in range(n):
        blocks_left_after = n - i - 1
        # keep remaining_extra distributable among the blocks that follow
        e_lo = max(0, remaining_extra - span * blocks_left_after)
        e_hi = min(span, remaining_extra)
        e = random.randint(e_lo, e_hi)
        sizes.append(lo + e)
        remaining_extra -= e

    random.shuffle(sizes)                      # spread randomness across blocks
    return sizes


def main():
    sizes = plan_sizes(TOTAL_TX, SIZE_MIN, SIZE_MAX)
    start = 1
    with open(OUT_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Block_id", "transaction_quantity", "remarks"])
        for i, qty in enumerate(sizes, start=1):
            end = start + qty - 1
            block_id = f"block_{i:04d}"
            remark = f"mem_pool tx_id {start}-{end}"
            w.writerow([block_id, qty, remark])
            start = end + 1
    total = sum(sizes)
    print(f"Wrote {len(sizes)} blocks, sizes in [{min(sizes)},{max(sizes)}], "
          f"total transactions = {total} -> {OUT_CSV}")
    assert total == TOTAL_TX, "block sizes do not sum to 500000!"
    assert all(SIZE_MIN <= s <= SIZE_MAX for s in sizes), "size out of range!"


if __name__ == "__main__":
    main()
