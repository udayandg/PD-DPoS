"""Aggregate the committee-size sweep archives into one comparison table.

Reads data/m{m}_k{k}/ for each config and computes, per system
(PDDPoS / DPoS): reward split (BP vs voters), profit-demand knapsack accept/reject
(PDDPoS), downgrades/slashing, and transaction validity. Writes a wide
data/sweep_summary.csv (one row per config x system) and prints a summary.
"""
import csv
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CONFIGS = [(10, 20), (20, 40), (30, 60), (40, 80)]
BP_REMARK = "self 20% + leftover"
VOTER_REMARK = "voter pd share"


def rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def reward_split(dist_rows, system):
    bp = [r for r in dist_rows if r["type"] == system and r["remarks"] == BP_REMARK]
    vt = [r for r in dist_rows if r["type"] == system and r["remarks"] == VOTER_REMARK]
    bp_tot = sum(float(r["pd_reward_stake"]) for r in bp)
    vt_tot = sum(float(r["pd_reward_stake"]) for r in vt)
    grand = bp_tot + vt_tot
    return {
        "reward_total": grand,
        "bp_total": bp_tot, "bp_share": bp_tot / grand * 100 if grand else 0,
        "bp_count": len(bp), "bp_mean": bp_tot / len(bp) if bp else 0,
        "voter_total": vt_tot, "voter_share": vt_tot / grand * 100 if grand else 0,
        "voter_count": len(vt), "voter_mean": vt_tot / len(vt) if vt else 0,
    }


def knapsack_stats(vote_rows):
    """PD-DPoS profit-demand accept/reject (voters only; self-votes excluded)."""
    voters = [r for r in vote_rows if r["account_id"] != r["candidate_account_id"]]
    active = [r for r in voters if r["Status"] == "active"]
    reject = [r for r in voters if r["Status"] == "rejected"]

    def mean_pd(rs):
        return sum(float(r["Profit_demand"]) for r in rs) / len(rs) if rs else 0.0

    def stake(rs):
        return sum(int(r["Stakes"]) for r in rs)

    return {
        "voters": len(voters), "accepted": len(active), "rejected": len(reject),
        "accept_pct": len(active) / len(voters) * 100 if voters else 0,
        "accepted_stake": stake(active), "rejected_stake": stake(reject),
        "mean_accepted_pd": mean_pd(active), "mean_rejected_pd": mean_pd(reject),
        "mean_all_pd": mean_pd(voters),
    }


def downgrade_stats(dlog_rows, system):
    d = [r for r in dlog_rows if r["type"] == system]
    mal = [r for r in d if int(r["slashing_pct"]) > 0]
    miss = [r for r in d if int(r["slashing_pct"]) == 0]
    return {"downgrades": len(d), "malicious": len(mal), "missed": len(miss)}


def block_stats(blocks_rows):
    incl = sum(int(r["n_tx"]) for r in blocks_rows)
    ign = sum(int(r["n_ignored"]) for r in blocks_rows)
    return {"blocks": len(blocks_rows), "tx_included": incl, "tx_ignored": ign}


def slashed_total(ledger_rows):
    return sum(float(r["total_slashed"]) for r in ledger_rows)


FIELDS = ["config", "m", "k", "committee", "system", "n_elections", "blocks",
          "tx_included", "tx_ignored", "reward_total", "bp_total", "bp_share",
          "bp_mean", "voter_total", "voter_share", "voter_count", "voter_mean",
          "downgrades", "malicious", "missed", "slashed_total",
          "voters", "accepted", "rejected", "accept_pct", "accepted_stake",
          "rejected_stake", "mean_accepted_pd", "mean_rejected_pd", "mean_all_pd"]


def main():
    out = []
    for m, k in CONFIGS:
        tag = f"m{m}_k{k}"
        d = os.path.join(DATA, tag)
        if not os.path.isdir(d):
            raise SystemExit(f"missing archive {d} - run run_sweep.py first.")
        dist = rows(os.path.join(d, "rewards_dist.csv"))
        vote = rows(os.path.join(d, "vote_pool.csv"))
        dlog = rows(os.path.join(d, "downgrade_log.csv"))
        einfo = rows(os.path.join(d, "election_info.csv"))
        ks = knapsack_stats(vote)
        n_elec = len(einfo)
        for system, blk_file, led_file in [
                ("PDDPoS", "blocks_produced.csv", "node_ledger.csv"),
                ("DPoS", "blocks_produced_dpos.csv", "node_ledger_dpos.csv")]:
            rs = reward_split(dist, system)
            bs = block_stats(rows(os.path.join(d, blk_file)))
            dg = downgrade_stats(dlog, system)
            sl = slashed_total(rows(os.path.join(d, led_file)))
            row = {"config": tag, "m": m, "k": k, "committee": m + k, "system": system,
                   "n_elections": n_elec, "slashed_total": round(sl, 2)}
            row.update({kk: (round(v, 2) if isinstance(v, float) else v)
                        for kk, v in {**rs, **bs, **dg}.items()})
            # profit-demand knapsack columns only meaningful for PD-DPoS
            if system == "PDDPoS":
                row.update({kk: (round(v, 3) if isinstance(v, float) else v)
                            for kk, v in ks.items()})
            out.append(row)

    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "sweep_summary.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in out:
            w.writerow({k: r.get(k, "") for k in FIELDS})

    # console summary
    print("committee | system | BP share% | voter share% | BP mean | voter mean | "
          "downgrades(mal/miss) | tx ignored")
    for r in out:
        print(f"  {r['committee']:>3}     | {r['system']:6} | {r['bp_share']:>7.2f} | "
              f"{r['voter_share']:>10.2f} | {r['bp_mean']:>7.2f} | {r['voter_mean']:>8.2f} | "
              f"{r['downgrades']:>2} ({r['malicious']}/{r['missed']})        | {r['tx_ignored']}")
    print("\nPD-DPoS profit-demand knapsack (accept/reject of voter votes):")
    for m, k in CONFIGS:
        tag = f"m{m}_k{k}"
        r = next(x for x in out if x["config"] == tag and x["system"] == "PDDPoS")
        print(f"  committee {m + k:>3}: accepted {r['accepted']}/{r['voters']} "
              f"({r['accept_pct']:.1f}%), mean accepted pd {r['mean_accepted_pd']:.3f} "
              f"vs rejected {r['mean_rejected_pd']:.3f}")
    print(f"\n-> {os.path.join(DATA, 'sweep_summary.csv')}")


if __name__ == "__main__":
    main()
