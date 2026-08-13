"""DPoS blockchain simulation engine (DPoS.md) - the DPoS counterpart to
pddpos_engine.py, run AFTER it so it can reuse the votes in inputs/vote_pool.csv.
"""
import csv
import os
import sys


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pddpos_engine as pd         
import bp_selection_pd              

INPUTS = pd.INPUTS
OUTPUT = pd.OUTPUT
TYPE = "DPoS"

# ---- DPoS-only files -------------------------------------------------------
BP_DPOS = os.path.join(INPUTS, "BP_nodes_dpos.csv")
CBP_DPOS = os.path.join(INPUTS, "CBP_nodes_dpos.csv")
ACCT_DPOS = os.path.join(INPUTS, "account_ledger_dpos.csv")
BLOCKS_DPOS = os.path.join(OUTPUT, "blocks_produced_dpos.csv")
LEDGER_DPOS = os.path.join(OUTPUT, "node_ledger_dpos.csv")
CHAIN_DPOS = os.path.join(OUTPUT, "chain_dpos.jsonl")
SUMMARY_DPOS = os.path.join(OUTPUT, "summary_dpos.md")

# ---- shared logs (append type=DPoS rows) ----------------------------------
SHARED_LOGS = [pd.REWARD_CSV, pd.REWARDS_DIST_CSV, pd.DOWNLOG_CSV]


def configure_pd():
    
    pd.TYPE = TYPE
    pd.BP_CSV = BP_DPOS
    pd.CBP_CSV = CBP_DPOS
    pd.ACCOUNT_LEDGER_CSV = ACCT_DPOS
    pd.BLOCKS_CSV = BLOCKS_DPOS
    pd.LEDGER_CSV = LEDGER_DPOS
    pd.CHAIN_JSONL = CHAIN_DPOS


def reset_dpos():

    for p in (BP_DPOS, CBP_DPOS, ACCT_DPOS, BLOCKS_DPOS, LEDGER_DPOS,
              CHAIN_DPOS, SUMMARY_DPOS):
        if os.path.exists(p):
            os.remove(p)
    for p in SHARED_LOGS:
        if not (os.path.exists(p) and os.path.getsize(p) > 0):
            continue
        with open(p, newline="") as f:
            rows = list(csv.reader(f))
        header = rows[0]
        ti = header.index("type")
        kept = [header] + [r for r in rows[1:] if len(r) <= ti or r[ti] != TYPE]
        with open(p, "w", newline="") as f:
            csv.writer(f).writerows(kept)
    print("DPoS reset: dpos files removed; type=DPoS rows stripped from shared logs.")


# ===========================================================================
# Inputs
# ===========================================================================
def load_election_info():
    """{eid: (n, m, k, subsidy)} from inputs/election_info.csv."""
    info = {}
    with open(pd.EINFO_CSV, newline="") as f:
        for r in csv.DictReader(f):
            info[int(r["Election_ID"])] = (int(r["n"]), int(r["m"]),
                                           int(r["k"]), int(r["subsidy"]))
    return info


def load_votes_by_election():
    """{eid: [(voter, candidate), ...]} from inputs/vote_pool.csv (order kept)."""
    votes = {}
    with open(pd.VOTE_CSV, newline="") as f:
        for r in csv.DictReader(f):
            votes.setdefault(int(r["Election_ID"]), []).append(
                (r["account_id"], r["candidate_account_id"]))
    return votes


# ===========================================================================
# DPoS election  ( - all votes, pd=1, DPoS-live stakes)
# ===========================================================================
def run_election_dpos(nodes, eid, m, k, votes):
    """Rank candidates by the sum of ALL their votes' stakes (no rejection; tie ->
    higher self-stake), write BP/CBP_dpos `init` rows, and return roles +
    active_voters (every voter, pd=1) for reward splitting.
"""
    active_voters = {}
    for voter, cand in votes:
        active_voters.setdefault(cand, []).append(
            (voter, nodes[voter]["stake"], 1.0))       # DPoS-live stake, pd=1

    ranked = [(c, nodes[c]["stake"], sum(stk for _, stk, _ in av))
              for c, av in active_voters.items()]
    ranked.sort(key=lambda t: (t[2], t[1]), reverse=True)   # active_total, self-stake

    bp, cbp = bp_selection_pd.write_bp_cbp_init(BP_DPOS, CBP_DPOS, eid, ranked, m, k)
    return {"bp": bp, "cbp": cbp, "active_voters": active_voters,
            "total_stake": sum(n["stake"] for n in nodes.values())}


# ===========================================================================
# Summary
# ===========================================================================
def write_summary_dpos(nodes, out, eids, n_cycles):
    total_reward = sum(r[5] for r in out["reward"])
    n_blocks = len(out["blocks"])
    total_incl = sum(r[5] for r in out["blocks"])
    total_ign = sum(r[14] for r in out["blocks"])
    n_mal = sum(1 for d in out["downgrade_log"] if d[4] > 0)
    n_miss = sum(1 for d in out["downgrade_log"] if d[4] == 0)
    total_slashed = sum(nd["slashed"] for nd in nodes.values())
    top = sorted(nodes.items(), key=lambda kv: kv[1]["stake"], reverse=True)[:5]

    lines = [
        "# DPoS Simulation Summary", "",
        f"- **Type**: {TYPE}",
        f"- **Elections replayed from vote_pool.csv**: {n_cycles} "
        f"(Election_ID {eids[0]}–{eids[-1]})",
        f"- **Blocks produced**: {n_blocks}",
        f"- **Transactions**: {total_incl:,} included / {total_ign:,} ignored "
        f"(insufficient funds) of 500,000 requests",
        f"- **Total reward minted+recycled**: {total_reward:,.2f}",
        f"- **Downgrades applied**: {len(out['downgrade_log'])} "
        f"(malicious={n_mal}, missed/invalid={n_miss})",
        f"- **Total stake slashed**: {total_slashed:,.2f}",
        "", "## Top 5 stakeholders (final)", "",
        "| account_id | final_stake | blocks_produced | total_rewards |",
        "|---|---|---|---|",
    ]
    for acc, nd in top:
        lines.append(f"| {acc} | {nd['stake']:,.2f} | {nd['produced']} | "
                     f"{nd['rewards']:,.2f} |")
    lines += [
        "", "## DPoS rules (vs PD-DPoS)",
        "- Votes taken from `vote_pool.csv`; **all** votes count (no knapsack rejection).",
        "- Every voter Profit-Demand = 1 -> reward 80% split is **stake-weighted** only; "
        "self keeps 20% + leftover.",
        "- Ranking + reward weights use **DPoS-live** stakes (own ledger from nodes_500.csv "
        "genesis, same mem_pool transactions + balance check).",
        "- Production / slashing / cooldown / chain identical to PD-DPoS (reused code).",
        "- DPoS rows appended (type=DPoS) to `reward.csv`, `rewards_dist.csv`, `downgrade_log.csv`.",
        "",
    ]
    with open(SUMMARY_DPOS, "w", newline="", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ===========================================================================
# Main
# ===========================================================================
def main():
    configure_pd()

    # ---- validate inputs BEFORE touching any DPoS state -------------------
    if not os.path.exists(pd.VOTE_CSV):
        raise SystemExit("inputs/vote_pool.csv not found - run pddpos_engine.py first.")
    info = load_election_info()
    votes_by_eid = load_votes_by_election()
    if not votes_by_eid:
        raise SystemExit("inputs/vote_pool.csv has no votes (header only) - run "
                         "pddpos_engine.py to generate them, then dpos_engine.py.")
    missing = sorted(e for e in votes_by_eid if e not in info)
    if missing:
        raise SystemExit(f"election_info.csv is missing Election_ID(s) {missing} that are "
                         "in vote_pool.csv - inputs are out of sync; re-run "
                         "pddpos_engine.py --clean, then dpos_engine.py.")

    reset_dpos()
    nodes = pd.load_ledger()                            # genesis nodes_500.csv
    mem = pd.load_mempool()
    blocksizes = pd.load_blocksizes()
    downgrades = pd.load_downgrades()

    out = {"reward": [], "dist": [], "blocks": [], "downgrade_log": [], "chain": []}
    acct_file, out["acct"] = pd.open_account_ledger(nodes)   # DPoS opening rows

    mem_ptr, bs_ptr = 0, 0
    n_total_blocks = len(blocksizes)
    cycle = 0
    election = None

    for eid in sorted(votes_by_eid):
        if bs_ptr >= n_total_blocks:
            break
        n, m, k, subsidy = info[eid]
        pd.M, pd.SUBSIDY = m, subsidy                  # produce_cycle reads these globals
        cycle += 1
        election = run_election_dpos(nodes, eid, m, k, votes_by_eid[eid])   
        n_blocks = min(n * m, n_total_blocks - bs_ptr)
        mem_ptr, bs_ptr = pd.produce_cycle(nodes, election, eid, cycle, n_blocks,
                                           mem, mem_ptr, blocksizes, bs_ptr,
                                           downgrades, out)                

    acct_file.close()
    out["acct"] = None
    if election is None or bs_ptr < n_total_blocks:
        raise SystemExit(f"vote_pool.csv elections produced only {bs_ptr}/{n_total_blocks} "
                         "blocks - inputs incomplete; re-run pddpos_engine.py --clean, "
                         "then dpos_engine.py.")
    last_bp = set(election["bp"])
    last_cbp = set(election["cbp"])

    pd.write_outputs(nodes, out, last_bp, last_cbp, append=True)   # dpos block/ledger + shared appends
    chain_blocks, chain_tx = pd.write_chain(out, append=False)    # fresh chain_dpos.jsonl
    write_summary_dpos(nodes, out, sorted(votes_by_eid), cycle)

    # ---- assertions -------------------------------------------------------
    total_incl = sum(r[5] for r in out["blocks"])
    total_ign = sum(r[14] for r in out["blocks"])
    assert mem_ptr == len(mem), f"requests not fully consumed: {mem_ptr}/{len(mem)}"
    assert bs_ptr == n_total_blocks
    assert total_incl + total_ign == 500000, "included + ignored != 500000 requests"
    assert len(out["blocks"]) == n_total_blocks
    assert chain_blocks == n_total_blocks, "chain block count mismatch"
    assert chain_tx == total_incl, "chain body tx count != included tx"

    print(f"DPoS run complete: {cycle} elections (from vote_pool.csv), "
          f"{len(out['blocks'])} blocks, {mem_ptr:,} requests.")
    print(f"  transactions: {total_incl:,} included, {total_ign:,} ignored")
    print(f"  downgrades applied: {len(out['downgrade_log'])}")
    print(f"  chain published: {chain_blocks} blocks / {chain_tx:,} txns -> {CHAIN_DPOS}")
    print(f"  outputs -> {OUTPUT}; BP/CBP_dpos + account_ledger_dpos -> {INPUTS}")


if __name__ == "__main__":
    main()
