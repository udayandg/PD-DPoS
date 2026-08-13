"""PD-DPoS full blockchain simulation engine """

import csv
import hashlib
import json
import math
import os
import random
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
INPUTS = os.path.join(ROOT, "inputs")
OUTPUT = os.path.join(ROOT, "output")


sys.path.insert(0, ROOT)
sys.path.insert(0, INPUTS)
import election_call        
import candidate_rating     
import vote_pool            
import bp_selection_pd      

# ---- input seeds (read-only) ----------------------------------------------
NODES_CSV = os.path.join(INPUTS, "nodes_500.csv")
MEMPOOL_CSV = os.path.join(INPUTS, "mem_pool.csv")
BLOCKSIZE_CSV = os.path.join(INPUTS, "block_size.csv")
DOWNGRADE_CSV = os.path.join(INPUTS, "downgrade.csv")

# ---- election trail (appended into inputs/) -------------------------------
EINFO_CSV = os.path.join(INPUTS, "election_info.csv")
ECANDID_CSV = os.path.join(INPUTS, "election_candid.csv")
ERATING_CSV = os.path.join(INPUTS, "election_candidates_rating.csv")
VOTE_CSV = os.path.join(INPUTS, "vote_pool.csv")
BP_CSV = os.path.join(INPUTS, "BP_nodes_pd.csv")
CBP_CSV = os.path.join(INPUTS, "CBP_nodes_pd.csv")

# Per-account movement ledger: one row per balance change (opening/send/receive/
# fee/reward/slash), running-balance semantics. Streamed during production.
ACCOUNT_LEDGER_CSV = os.path.join(INPUTS, "account_ledger.csv")
ACCT_HEADER = ["account_id", "opening_stake", "transaction_ref", "block_ref",
               "amount", "closing_stake", "remarks"]

# ---- run results (written to output/) -------------------------------------
REWARD_CSV = os.path.join(OUTPUT, "reward.csv")
REWARDS_DIST_CSV = os.path.join(OUTPUT, "rewards_dist.csv")
BLOCKS_CSV = os.path.join(OUTPUT, "blocks_produced.csv")
LEDGER_CSV = os.path.join(OUTPUT, "node_ledger.csv")
DOWNLOG_CSV = os.path.join(OUTPUT, "downgrade_log.csv")
SUMMARY_MD = os.path.join(OUTPUT, "summary.md")
CHAIN_JSONL = os.path.join(OUTPUT, "chain.jsonl")
GENESIS_HASH = "0" * 64

# ---- parameters -----------------------------------------------------------
# Election weighting (34/33/33) and per-step RNG seeds live in the step modules
# (candidate_rating, vote_pool, election_call).
M, K, N = 5, 10, 10          # active producers, backups, rounds per election
SUBSIDY = 128
COOLDOWN_Q = 5               # Q rounds of cooldown (reported; source: downgrade.csv)
SELF_PD = 1.0                # self-stake profit demand x (configurable), -> vote_pool
PD_LOW, PD_HIGH = 0.5, 1.5   # voter profit-demand range (user-given), -> vote_pool
PROD_COST = 0.20             # 20% production cost kept by producer
TYPE = "PDDPoS"

# ---- trail headers (must match existing files) ----------------------------
EINFO_HEADER = ["Election_ID", "n", "m", "k", "subsidy", " remarks"]
ECANDID_HEADER = ["Election_ID", "account_id", "stake", "processing", "k_shell"]
ERATING_HEADER = ["Election_ID", "account_id", "norm_stake", "norm_processing",
                  "norm_k-shell", "w_stake", "w_processing", "w_k-shell",
                  "Total", "Remarks"]
VOTE_HEADER = ["Election_ID", "account_id", "candidate_account_id",
               "Profit_demand", "Stakes", "Status", "Remarks"]
# BP/CBP files are a live audit log of the round-robin schedule: an `init` row per
# elected node, then one row per schedule mutation during production. BP events:
# init | promoted-in | downgraded-out.  CBP events: init | promoted-out |
# enqueued | cooldown | rejoin.  `block_id` is blank on init rows.
BP_HEADER = ["Election_ID", "block_id", "account_id", "stake", "event",
             "Timestamp", "remarks"]


# ===========================================================================
# Hashing / chain helpers
# ===========================================================================
def _sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


def merkle_root(body):
    """Merkle root over a block body of (tx_id, from, to, amount, fee) tuples."""
    if not body:
        return _sha("")
    layer = [_sha(f"{t[0]}|{t[1]}|{t[2]}|{t[3]}|{t[4]}") for t in body]
    while len(layer) > 1:
        nxt = []
        for i in range(0, len(layer), 2):
            b = layer[i + 1] if i + 1 < len(layer) else layer[i]   # duplicate last
            nxt.append(_sha(layer[i] + b))
        layer = nxt
    return layer[0]


def block_hash(blk, prev_hash, mroot):
    header_str = "|".join(str(x) for x in (
        blk["block_id"], blk["election_id"], blk["round_id"], blk["producer"],
        blk["timestamp"], blk["n_tx"], blk["t_fees"], blk["subsidy"],
        blk["total_reward"], prev_hash, mroot))
    return _sha(header_str)


# ===========================================================================
# Loading
# ===========================================================================
def load_ledger():
    nodes = {}
    with open(NODES_CSV, newline="") as f:
        for r in csv.DictReader(f):
            stake = float(r["stake"])
            nodes[r["account_id"]] = {
                "stake": stake, "initial_stake": stake,
                "produced": 0, "rewards": 0.0, "slashed": 0.0, "downgrades": 0,
            }
    return nodes


def load_mempool():
    """Return list of (from, to, amount, fee) in tx_id order."""
    txns = []
    with open(MEMPOOL_CSV, newline="") as f:
        for r in csv.DictReader(f):
            txns.append((r["from_account"], r["to_account"],
                         int(r["amount"]), int(r["Tx_fee"])))
    return txns


def load_blocksizes():
    with open(BLOCKSIZE_CSV, newline="") as f:
        return [(r["Block_id"], int(r["transaction_quantity"]))
                for r in csv.DictReader(f)]


def load_downgrades():
    dg = {}
    with open(DOWNGRADE_CSV, newline="") as f:
        for r in csv.DictReader(f):
            dg[r["Block_id"]] = (int(r["slashing"]), int(r["cooldown"]))
    return dg


def current_max_election_id():
    """Max Election_ID in the trail; 0 when the trail holds only its header
    (a clean base) so the first cycle starts at Election_ID 1."""
    ids = []
    if os.path.exists(EINFO_CSV) and os.path.getsize(EINFO_CSV) > 0:
        with open(EINFO_CSV, newline="") as f:
            for r in csv.DictReader(f):
                try:
                    ids.append(int(r["Election_ID"]))
                except (KeyError, ValueError):
                    pass
    return max(ids) if ids else 0


# ===========================================================================
# Election 
# ===========================================================================
def run_election(nodes, eid):
  
    accounts = list(nodes.keys())
    stake_of = {a: nodes[a]["stake"] for a in accounts}
    total_stake = sum(stake_of.values())

    election_call.trigger(ECANDID_CSV, EINFO_CSV, eid, N, M, K, SUBSIDY,
                          accounts, stake_of, random.Random(1000 + eid))
    candidate_rating.rate(ECANDID_CSV, ERATING_CSV, eid)
    vote_pool.build_and_append(ERATING_CSV, VOTE_CSV, eid, accounts, stake_of,
                               SELF_PD, random.Random(2000 + eid), PD_LOW, PD_HIGH)
    # Ranking stake re-derived from the LIVE PD-DPoS ledger (stake_of), not the
    # recorded vote_pool Stakes -> PDDPoS-live ranking (mirrors DPoS-live in dpos_engine).
    bp, cbp, active_voters = bp_selection_pd.select(VOTE_CSV, BP_CSV, CBP_CSV,
                                                    eid, M, K, stake_of)

    return {"bp": bp, "cbp": cbp, "active_voters": active_voters,
            "total_stake": total_stake}


def read_schedule(eid):
    """File-driven : read this election's `init` rows back from the BP/CBP
    CSVs, in rank order, to seed the round-robin queue and the backup queue."""
    def _init_accounts(path):
        accts = []
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                if int(r["Election_ID"]) == eid and r["event"] == "init":
                    accts.append(r["account_id"])
        return accts
    return _init_accounts(BP_CSV), _init_accounts(CBP_CSV)


def append_schedule_event(path, eid, block_id, account, stake, event, remark):
    """Append one schedule-mutation row to a BP/CBP audit file ."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _append_trail(path, [[eid, block_id, account, int(round(stake)), event,
                          ts, remark]])


# ===========================================================================
# Account movement ledger  (inputs/account_ledger.csv)
# ===========================================================================
def acct_row(out, account, before, after, tx_ref, block_ref, remark):
    """Stream one running-balance row to account_ledger.csv via out['acct'].
    `amount` is derived from the rounded balances (closing - opening) so every
    row satisfies opening + amount == closing exactly and openings chain to the
    prior closing. tx_ref/block_ref blank -> 'Null'; no-op if writer absent."""
    w = out.get("acct")
    if w is None:
        return
    op, cl = round(before, 2), round(after, 2)
    w.writerow([account, op, tx_ref if tx_ref is not None else "Null",
                block_ref if block_ref is not None else "Null",
                round(cl - op, 2), cl, remark])


# ===========================================================================
# Reward distribution 
# ===========================================================================
def distribute(nodes, producer, reward, voters, block_id, out):
    """20% self + leftover; 80% to active voters by (stake x pd). Returns
    (self_total, distributed)."""
    self_keep = PROD_COST * reward
    pot = (1 - PROD_COST) * reward
    weighted = [(v, stk * pd) for (v, stk, pd) in voters]
    tw = sum(w for _, w in weighted)

    distributed = 0.0
    for (v, w) in weighted:
        if v == producer:
            continue                                   # self handled below
        share = pot * w / tw if tw > 0 else 0.0
        if share:
            before = nodes[v]["stake"]
            nodes[v]["stake"] += share
            nodes[v]["rewards"] += share
            out["dist"].append([None, block_id, TYPE, v, round(before, 2),
                                round(share, 2), round(nodes[v]["stake"], 2),
                                "voter pd share"])
            acct_row(out, v, before, nodes[v]["stake"], None, block_id,
                     "reward pd share")
            distributed += share

    leftover = pot - distributed
    self_total = self_keep + leftover
    before = nodes[producer]["stake"]
    nodes[producer]["stake"] += self_total
    nodes[producer]["rewards"] += self_total
    out["dist"].append([None, block_id, TYPE, producer, round(before, 2),
                        round(self_total, 2), round(nodes[producer]["stake"], 2),
                        "self 20% + leftover"])
    acct_row(out, producer, before, nodes[producer]["stake"], None,
             block_id, "reward self 20%+leftover")
    return self_total, distributed


# ===========================================================================
# Production of one election cycle  
# ===========================================================================
def produce_cycle(nodes, election, eid, cycle, n_blocks,
                  mem, mem_ptr, blocksizes, bs_ptr, downgrades, out):
    # seed the round-robin queue + backup queue from the
    # BP/CBP `init` rows just written to the CSVs for this election.
    active, backups = read_schedule(eid)   # active len m, backups len k
    active_voters = election["active_voters"]
    cooldown = {}                          # node -> rounds remaining

    for s in range(n_blocks):
        round_id = s // M + 1
        block_id, n_req = blocksizes[bs_ptr]
        bs_ptr += 1

        # This block's transaction requests, sliced from the mempool in tx_id
        # order (consumed now; applied only if the block is published below).
        base = mem_ptr
        reqs = mem[base:base + n_req]
        mem_ptr += n_req

        if not active:                     # no producers left -> block NOT published
            # Requests dropped: nothing applied, no ledger rows, no subsidy minted.
            out["blocks"].append([eid, round_id, block_id, "", "", 0, 0, 0, 0,
                                  0, 0, "skipped-no-producer", 0, "", n_req])
            continue

        pos = s % len(active)
        scheduled = active[pos]
        dg = downgrades.get(block_id)

        event, slashed_amt, promoted = "normal", 0.0, None
        if dg is None:
            actual = scheduled
        elif dg[0] > 0:                    # (10,5) malicious 9.3.1 - block kept
            event, actual = "malicious", scheduled
        else:                              # (0,0) missed/invalid 9.3.2
            event = "missed"
            actual = active[(pos + 1) % len(active)] if len(active) > 1 else scheduled


        t_fees = 0
        body = []
        n_ignored = 0
        for i, (frm, to, amt, fee) in enumerate(reqs):
            if nodes[frm]["stake"] < amt + fee:        # insufficient funds -> ignore
                n_ignored += 1
                continue
            tx_id = base + i + 1                        # mem is in tx_id order
            body.append((tx_id, frm, to, amt, fee))
            
            b = nodes[frm]["stake"]; nodes[frm]["stake"] = b - amt
            acct_row(out, frm, b, nodes[frm]["stake"], tx_id, block_id,
                     f"send to {to}")
            b = nodes[frm]["stake"]; nodes[frm]["stake"] = b - fee
            acct_row(out, frm, b, nodes[frm]["stake"], tx_id, block_id, "tx_fee")
            b = nodes[to]["stake"]; nodes[to]["stake"] = b + amt
            acct_row(out, to, b, nodes[to]["stake"], tx_id, block_id,
                     f"receive from {frm}")
            t_fees += fee
        n_incl = len(body)
        reward = t_fees + SUBSIDY

        # reward the actual publisher and its voters.
        voters = active_voters.get(actual, [(actual, nodes[actual]["stake"], SELF_PD)])
        self_keep, dist = distribute(nodes, actual, reward, voters, block_id, out)
        nodes[actual]["produced"] += 1
        out["reward"].append([block_id, TYPE, actual, eid, round_id, round(reward, 2)])

        # block (header computed at write time) published to chain.
        out["chain"].append({
            "block_id": block_id, "election_id": eid, "round_id": round_id,
            "producer": actual,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "n_tx": n_incl, "t_fees": t_fees, "subsidy": SUBSIDY,
            "total_reward": round(reward, 2), "body": body,
        })
        _ = cycle  # (cycle retained in signature; records key on Election_ID)

        # apply the downgrade to the scheduled node.
        if dg is not None:
            slash_pct, cd = dg
            kind = "malicious 9.3.1" if slash_pct > 0 else "missed/invalid 9.3.2"
            if slash_pct > 0:
                s_amt = 0.10 * nodes[scheduled]["stake"]
                before = nodes[scheduled]["stake"]
                nodes[scheduled]["stake"] -= s_amt
                nodes[scheduled]["slashed"] += s_amt
                slashed_amt = s_amt
                acct_row(out, scheduled, before, nodes[scheduled]["stake"],
                         None, block_id, "slash 10% malicious")
            nodes[scheduled]["downgrades"] += 1
            if scheduled in active:
                active.remove(scheduled)
            # BP audit: scheduled node removed from the active queue.
            append_schedule_event(BP_CSV, eid, block_id, scheduled,
                                  nodes[scheduled]["stake"], "downgraded-out",
                                  f"{kind}; slashed={round(slashed_amt, 2)}")
            if backups:
                promoted = backups.pop(0)
                active.insert(min(pos, len(active)), promoted)
                # CBP audit: backup leaves queue; BP audit: it joins active.
                append_schedule_event(CBP_CSV, eid, block_id, promoted,
                                      nodes[promoted]["stake"], "promoted-out",
                                      f"promoted to replace {scheduled}")
                append_schedule_event(BP_CSV, eid, block_id, promoted,
                                      nodes[promoted]["stake"], "promoted-in",
                                      f"replaces {scheduled} at slot {pos}")
            if cd > 0:
                cooldown[scheduled] = cd           # rejoin backups after Q rounds
                append_schedule_event(CBP_CSV, eid, block_id, scheduled,
                                      nodes[scheduled]["stake"], "cooldown",
                                      f"waits {cd} rounds before rejoining backups")
            else:
                backups.append(scheduled)
                append_schedule_event(CBP_CSV, eid, block_id, scheduled,
                                      nodes[scheduled]["stake"], "enqueued",
                                      "appended to backups queue (no cooldown)")
            out["downgrade_log"].append([eid, block_id, scheduled, TYPE,
                                         slash_pct, cd, promoted or ""])

        out["blocks"].append([eid, round_id, block_id, scheduled, actual, n_incl,
                              t_fees, SUBSIDY, round(reward, 2), round(self_keep, 2),
                              round(dist, 2), event, round(slashed_amt, 2), "",
                              n_ignored])

        # end of round: tick cooldowns; return finished nodes to backups.
        if (s + 1) % M == 0 or s == n_blocks - 1:
            for nd in list(cooldown):
                cooldown[nd] -= 1
                if cooldown[nd] <= 0:
                    backups.append(nd)
                    del cooldown[nd]
                    # CBP audit: cooldown finished, node returns to backups.
                    append_schedule_event(CBP_CSV, eid, block_id, nd,
                                          nodes[nd]["stake"], "rejoin",
                                          "cooldown complete; back in backups queue")

    return mem_ptr, bs_ptr


# ===========================================================================
# Output writers
# ===========================================================================
def _append_trail(path, rows):
    if not rows:
        return
    with open(path, "a", newline="") as f:
        csv.writer(f).writerows(rows)


def _write_csv(path, header, rows, append):
    """Write rows; append (no re-header) when append=True and file non-empty."""
    exists = os.path.exists(path) and os.path.getsize(path) > 0
    mode = "a" if (append and exists) else "w"
    with open(path, mode, newline="") as f:
        w = csv.writer(f)
        if mode == "w":
            w.writerow(header)
        w.writerows(rows)


def _next_id(path, col, append):
    """Next sequential id continuing from an existing file's max (append)."""
    if append and os.path.exists(path) and os.path.getsize(path) > 0:
        mx = 0
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                try:
                    mx = max(mx, int(r[col]))
                except (KeyError, ValueError):
                    pass
        return mx + 1
    return 1


def _migrate_first_col(path, old_name):
    """One-time schema fix: rename a leading 'Cycle' column to 'Election_ID'.

    Prior runs keyed logs on Cycle (1..) with start_eid=1, so Election_ID = 1+Cycle.
    """
    if not (os.path.exists(path) and os.path.getsize(path) > 0):
        return
    with open(path, newline="") as f:
        rows = list(csv.reader(f))
    if not rows or rows[0][0] != old_name:
        return
    rows[0][0] = "Election_ID"
    for r in rows[1:]:
        if r:
            r[0] = str(1 + int(r[0]))
    with open(path, "w", newline="") as f:
        csv.writer(f).writerows(rows)


def write_outputs(nodes, out, last_bp, last_cbp, append):
    os.makedirs(OUTPUT, exist_ok=True)

    _write_csv(REWARD_CSV,
               ["Block_id", "type", "account_id", "Election_id", "round_id",
                "Total_rewards"], out["reward"], append)

    start_id = _next_id(REWARDS_DIST_CSV, "Reward_dist_id", append)
    for i, row in enumerate(out["dist"], start=start_id):
        row[0] = i
    _write_csv(REWARDS_DIST_CSV,
               ["Reward_dist_id", "Block_id", "type", "account_id", "self.stake",
                "pd_reward_stake", "closing_stake", "remarks"], out["dist"], append)

    # solidification : solidified once ceil(2m/3) valid blocks follow
    depth = math.ceil(2 * M / 3)
    total_blocks = len(out["blocks"])
    for i, row in enumerate(out["blocks"]):
        row[13] = "yes" if (total_blocks - (i + 1)) >= depth else "no"
    _migrate_first_col(BLOCKS_CSV, "Cycle")
    _write_csv(BLOCKS_CSV,
               ["Election_ID", "Round", "Block_id", "scheduled_producer",
                "actual_producer", "n_tx", "T_fees", "subsidy", "total_reward",
                "self_keep", "dist_to_voters", "event", "slashed", "solidified",
                "n_ignored"],
               out["blocks"], append)

    _migrate_first_col(DOWNLOG_CSV, "Cycle")
    _write_csv(DOWNLOG_CSV,
               ["Election_ID", "Block_id", "node", "type", "slashing_pct",
                "cooldown_rounds", "promoted_backup"], out["downgrade_log"], append)

    # node_ledger is a snapshot of THIS run's final state (always overwritten).
    with open(LEDGER_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["account_id", "initial_stake", "final_stake", "status",
                    "blocks_produced", "total_rewards", "total_slashed",
                    "times_downgraded"])
        for acc, nd in nodes.items():
            status = "BP" if acc in last_bp else "CBP" if acc in last_cbp else "voter"
            w.writerow([acc, round(nd["initial_stake"], 2), round(nd["stake"], 2),
                        status, nd["produced"], round(nd["rewards"], 2),
                        round(nd["slashed"], 2), nd["downgrades"]])


def _last_block_hash(append):
    """Continue the chain from the last published block_hash (append), else genesis."""
    if append and os.path.exists(CHAIN_JSONL) and os.path.getsize(CHAIN_JSONL) > 0:
        last = None
        with open(CHAIN_JSONL, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last = line
        if last:
            return json.loads(last)["header"]["block_hash"]
    return GENESIS_HASH


def write_chain(out, append):
    """Publish blocks to output/chain.jsonl - one block per line, header + body.

    Blocks are in global production order; each header links to the previous
    block_hash and carries the merkle root of its body. Returns (n_blocks, n_tx).
    """
    prev = _last_block_hash(append)
    exists = os.path.exists(CHAIN_JSONL) and os.path.getsize(CHAIN_JSONL) > 0
    mode = "a" if (append and exists) else "w"
    n_tx = 0
    with open(CHAIN_JSONL, mode, newline="", encoding="utf-8") as f:
        for blk in out["chain"]:
            mroot = merkle_root(blk["body"])
            bhash = block_hash(blk, prev, mroot)
            header = {
                "block_id": blk["block_id"], "type": TYPE,
                "election_id": blk["election_id"], "round_id": blk["round_id"],
                "producer": blk["producer"], "timestamp": blk["timestamp"],
                "n_tx": blk["n_tx"], "T_fees": blk["t_fees"],
                "subsidy": blk["subsidy"], "total_reward": blk["total_reward"],
                "prev_hash": prev, "merkle_root": mroot, "block_hash": bhash,
            }
            body = [{"tx_id": t[0], "from_account": t[1], "to_account": t[2],
                     "amount": t[3], "Tx_fee": t[4]} for t in blk["body"]]
            f.write(json.dumps({"header": header, "body": body}) + "\n")
            prev = bhash
            n_tx += len(body)
    return len(out["chain"]), n_tx


def write_summary(nodes, out, first_eid, last_eid, n_cycles):
    total_reward = sum(r[5] for r in out["reward"])
    n_blocks = len(out["blocks"])
    total_tx = sum(r[5] for r in out["blocks"])            # included
    total_ignored = sum(r[14] for r in out["blocks"])      # dropped (insufficient funds)
    n_mal = sum(1 for d in out["downgrade_log"] if d[4] > 0)
    n_miss = sum(1 for d in out["downgrade_log"] if d[4] == 0)
    total_slashed = sum(nd["slashed"] for nd in nodes.values())
    top = sorted(nodes.items(), key=lambda kv: kv[1]["stake"], reverse=True)[:5]

    lines = [
        "# PD-DPoS Simulation Summary", "",
        f"- **Type**: {TYPE}",
        f"- **Config**: m={M}, k={K}, n={N}, subsidy={SUBSIDY}, cooldown Q={COOLDOWN_Q}, "
        f"production cost={PROD_COST:.0%}, self pd={SELF_PD}, "
        f"voter pd∈[{PD_LOW},{PD_HIGH}]",
        f"- **Election cycles**: {n_cycles} (Election_ID {first_eid}–{last_eid})",
        f"- **Blocks produced**: {n_blocks}",
        f"- **Transactions**: {total_tx:,} included / {total_ignored:,} ignored "
        f"(insufficient funds) of 500,000 mem_pool requests",
        f"- **Total reward minted+recycled**: {total_reward:,.2f} "
        f"(subsidy minted = {SUBSIDY * n_blocks:,})",
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
        "", "## Notes",
        "- A transaction is **included only if the sender holds amount + Tx_fee** at "
        "that point; otherwise the request is ignored (dropped, not retried).",
        "- Transactions move **stake** directly (sender − amount − fee, receiver + amount); "
        "stake never goes negative from transfers.",
        "- Rewards compound into stake; stakes carry forward across cycles (not reset).",
        "- Processing & k-shell are re-randomised each election.",
        "- Reward 80% split ∝ (active_stake × profit_demand); self keeps 20% + leftover.",
        f"- Chain published to `chain.jsonl`: {len(out['chain'])} blocks, each with a "
        "header (prev_hash link + merkle_root + block_hash) and its transaction body.",
        "",
    ]
    with open(SUMMARY_MD, "w", newline="", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ===========================================================================
# Main loop  
# ===========================================================================
# Trail files reset to header-only on a clean regenerate.
TRAIL_RESETS = [
    (EINFO_CSV, EINFO_HEADER),
    (ECANDID_CSV, ECANDID_HEADER),
    (ERATING_CSV, ERATING_HEADER),
    (VOTE_CSV, VOTE_HEADER),
    (BP_CSV, BP_HEADER),
    (CBP_CSV, BP_HEADER),
    (ACCOUNT_LEDGER_CSV, ACCT_HEADER),
]
OUTPUT_RESETS = [REWARD_CSV, REWARDS_DIST_CSV, BLOCKS_CSV, LEDGER_CSV,
                 DOWNLOG_CSV, SUMMARY_MD, CHAIN_JSONL]


def reset_state():
    """Clean regenerate: truncate the inputs/ election trail to headers only
    (fixing any prior corruption) and remove the output/ deliverables so the
    run rebuilds them from Election_ID 1."""
    os.makedirs(INPUTS, exist_ok=True)
    os.makedirs(OUTPUT, exist_ok=True)
    for path, header in TRAIL_RESETS:
        with open(path, "w", newline="") as f:
            csv.writer(f).writerow(header)
    for path in OUTPUT_RESETS:
        if os.path.exists(path):
            os.remove(path)
    print("Clean regenerate: election trail reset to headers; output/ cleared.")


def read_params():
    """Resolve n, m, k, subsidy, clean-flag and append choice.

    CLI (non-interactive):  python pddpos_engine.py [--clean] n m k [subsidy] [pd_low] [pd_high] [self_pd]
    Otherwise prompts interactively, falling back to defaults on EOF/bad input.
    On a clean run the trail/output are reset and Election_ID restarts at 1.
    """
    global N, M, K, SUBSIDY, PD_LOW, PD_HIGH, SELF_PD
    args = sys.argv[1:]
    clean = "--clean" in args
    nums = [a for a in args if a != "--clean"]

    if len(nums) >= 3:
        N, M, K = int(nums[0]), int(nums[1]), int(nums[2])
        if len(nums) >= 4:
            SUBSIDY = int(nums[3])
        if len(nums) >= 6:
            PD_LOW, PD_HIGH = float(nums[4]), float(nums[5])
        if len(nums) >= 7:
            SELF_PD = float(nums[6])
        append = True
    else:
        try:
            N = int(input("Enter n (rounds of block production per election): ").strip())
            M = int(input("Enter m (active block producers): ").strip())
            K = int(input("Enter k (backup producers): ").strip())
            s = input(f"Enter subsidy [{SUBSIDY}]: ").strip()
            if s:
                SUBSIDY = int(s)
            lo = input(f"Enter voter profit-demand LOW [{PD_LOW}]: ").strip()
            if lo:
                PD_LOW = float(lo)
            hi = input(f"Enter voter profit-demand HIGH [{PD_HIGH}]: ").strip()
            if hi:
                PD_HIGH = float(hi)
            x = input(f"Enter self-stake profit demand x [{SELF_PD}]: ").strip()
            if x:
                SELF_PD = float(x)
            ans = input("Clean regenerate (reset trail+output)? (y/n) [n]: ").strip().lower()
            clean = ans == "y"
            ans = input("Append to existing records? (y/n) [y]: ").strip().lower()
            append = ans != "n"
        except (EOFError, ValueError):
            print("No/invalid input -> defaults: n=10, m=5, k=10, subsidy=128, "
                  "pd=[0.5,1.5], append=yes")
            N, M, K, append = 10, 5, 10, True

    if PD_LOW > PD_HIGH:                        # normalise a reversed range
        PD_LOW, PD_HIGH = PD_HIGH, PD_LOW
    if M + K + 1 > len(load_ledger()):
        raise SystemExit(f"m+k too large: need > {M + K} candidate slots among nodes")
    if clean:
        reset_state()
    print(f"Running PD-DPoS with n={N}, m={M}, k={K}, subsidy={SUBSIDY}, "
          f"pd=[{PD_LOW},{PD_HIGH}], self_pd={SELF_PD}, clean={clean}, append={append}")
    return append


def open_account_ledger(nodes):
    """Open inputs/account_ledger.csv for streaming; on a fresh file seed one
    `opening balance` row per account (running balance starts at initial stake).
    Returns (file_handle, csv_writer)."""
    exists = os.path.exists(ACCOUNT_LEDGER_CSV) and os.path.getsize(ACCOUNT_LEDGER_CSV) > 0
    data_rows = 0
    if exists:
        with open(ACCOUNT_LEDGER_CSV, newline="") as f:
            data_rows = sum(1 for _ in f) - 1          # minus header
    f = open(ACCOUNT_LEDGER_CSV, "a", newline="")
    w = csv.writer(f)
    if not exists:
        w.writerow(ACCT_HEADER)
    if data_rows <= 0:                                 # fresh: seed opening balances
        for acc, nd in nodes.items():
            s = round(nd["stake"], 2)
            w.writerow([acc, s, "Null", "Null", 0, s, "opening balance"])
    return f, w


def main():
    append = read_params()
    nodes = load_ledger()                              
    mem = load_mempool()
    blocksizes = load_blocksizes()
    downgrades = load_downgrades()
    start_eid = current_max_election_id()

    out = {"reward": [], "dist": [], "blocks": [], "downgrade_log": [], "chain": []}
    # Stream every balance movement to inputs/account_ledger.csv (opening rows now).
    acct_file, out["acct"] = open_account_ledger(nodes)

    mem_ptr, bs_ptr = 0, 0
    n_total_blocks = len(blocksizes)
    cycle = 0
    election = None

    while bs_ptr < n_total_blocks:
        cycle += 1
        eid = start_eid + cycle                        # continue Election_ID
        election = run_election(nodes, eid)                 
        n_blocks = min(N * M, n_total_blocks - bs_ptr)       
        mem_ptr, bs_ptr = produce_cycle(nodes, election, eid, cycle, n_blocks,
                                        mem, mem_ptr, blocksizes, bs_ptr,
                                        downgrades, out)      

    acct_file.close()                                  # flush the account ledger
    out["acct"] = None
    last_bp = set(election["bp"])
    last_cbp = set(election["cbp"])

    write_outputs(nodes, out, last_bp, last_cbp, append)
    chain_blocks, chain_tx = write_chain(out, append)
    write_summary(nodes, out, start_eid + 1, start_eid + cycle, cycle)

    # ---- assertions -------------------------------------------------------
    total_incl = sum(r[5] for r in out["blocks"])          # included transactions
    total_ign = sum(r[14] for r in out["blocks"])          # ignored (insufficient funds / unpublished)
    assert mem_ptr == len(mem), f"requests not fully consumed: {mem_ptr}/{len(mem)}"
    assert bs_ptr == n_total_blocks
    assert total_incl + total_ign == 500000, "included + ignored != 500000 requests"
    assert len(out["blocks"]) == n_total_blocks
    assert chain_blocks == n_total_blocks, "chain block count mismatch"
    assert chain_tx == total_incl, "chain body tx count != included tx"

    print(f"PD-DPoS run complete: {cycle} election cycles "
          f"(Election_ID {start_eid + 1}-{start_eid + cycle}), "
          f"{len(out['blocks'])} blocks, {mem_ptr:,} requests.")
    print(f"  transactions: {total_incl:,} included, {total_ign:,} ignored "
          f"(insufficient funds / unpublished)")
    print(f"  downgrades applied: {len(out['downgrade_log'])}")
    print(f"  chain published: {chain_blocks} blocks / {chain_tx:,} txns -> {CHAIN_JSONL}")
    print(f"  outputs -> {OUTPUT}")
    print(f"  election trail + account_ledger.csv appended -> {INPUTS}")


if __name__ == "__main__":
    main()
