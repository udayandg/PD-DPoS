"""PDDPoS vs DPoS reward-distribution analysis.

Reads output/rewards_dist.csv (which holds both PDDPoS and DPoS reward rows) and
splits every block's reward into two groups off the `remarks` column:
  * BP     = "self 20% + leftover"  (the producer's keep, one row per block)
  * Voter  = "voter pd share"       (the 80% pot split across active voters)

It then runs four comparisons on `pd_reward_stake`:
  1. DPoS    : BP  vs all Voters       2. PDDPoS  : BP  vs all Voters
  3. BP      : DPoS vs PDDPoS          4. Voters  : DPoS vs PDDPoS

Outputs (to output/): reward_analysis.md, reward_analysis.csv, reward_analysis.png.
Pure stdlib for the numbers; matplotlib for the chart.
"""
import csv
import os

import matplotlib
matplotlib.use("Agg")                       # headless PNG backend
import matplotlib.pyplot as plt

ROOT = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(ROOT, "output")
DIST_CSV = os.path.join(OUTPUT, "rewards_dist.csv")
MD_OUT = os.path.join(OUTPUT, "reward_analysis.md")
CSV_OUT = os.path.join(OUTPUT, "reward_analysis.csv")
PNG_OUT = os.path.join(OUTPUT, "reward_analysis.png")

BP_REMARK = "self 20% + leftover"
VOTER_REMARK = "voter pd share"
TYPES = ("PDDPoS", "DPoS")
GROUPS = ("BP", "Voter")

# ---- palette (dataviz reference; light surface) ---------------------------
C = {"PDDPoS": "#2a78d6", "DPoS": "#eb6834"}     # system colours (blue / orange)
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, BASE, SURFACE = "#e1e0d9", "#c3c2b7", "#fcfcfb"


# ===========================================================================
# Aggregate
# ===========================================================================
def load_totals():
    """Return {(type, group): [count, total_reward]} from rewards_dist.csv."""
    if not os.path.exists(DIST_CSV):
        raise SystemExit(f"{DIST_CSV} not found - run pddpos_engine.py then dpos_engine.py first.")
    agg = {}
    with open(DIST_CSV, newline="") as f:
        for r in csv.DictReader(f):
            grp = "BP" if r["remarks"] == BP_REMARK else \
                  "Voter" if r["remarks"] == VOTER_REMARK else None
            if grp is None:
                continue
            a = agg.setdefault((r["type"], grp), [0, 0.0])
            a[0] += 1
            a[1] += float(r["pd_reward_stake"])
    for t in TYPES:
        if (t, "BP") not in agg or (t, "Voter") not in agg:
            raise SystemExit(f"No {t} rows in rewards_dist.csv - run "
                             f"{'pddpos_engine.py' if t == 'PDDPoS' else 'dpos_engine.py'} first.")
    return agg


def build_metrics(agg):
    """Per-system BP/Voter totals, counts, shares, means; plus cross-system deltas."""
    m = {}
    for t in TYPES:
        grand = agg[(t, "BP")][1] + agg[(t, "Voter")][1]
        m[t] = {"grand": grand}
        for g in GROUPS:
            cnt, tot = agg[(t, g)]
            m[t][g] = {"total": tot, "count": cnt,
                       "share": tot / grand * 100 if grand else 0.0,
                       "mean": tot / cnt if cnt else 0.0}

    def cross(group):
        d, p = m["DPoS"][group]["total"], m["PDDPoS"][group]["total"]
        return {"dpos": d, "pddpos": p, "diff": d - p,
                "ratio": d / p if p else float("inf"),
                "pct": (d - p) / p * 100 if p else float("inf")}

    return m, {"BP": cross("BP"), "Voter": cross("Voter")}


# ===========================================================================
# Reports
# ===========================================================================
def _bar(share, width=22):
    return "#" * round(share / 100 * width)


def write_console_and_md(m, cross):
    L = []
    L.append("# PDDPoS vs DPoS - Reward Distribution Analysis\n")
    L.append(f"- Source: `output/rewards_dist.csv` | reward metric: `pd_reward_stake`")
    L.append(f"- **BP** = producer self-keep (20% + leftover); **Voter** = 80% pot "
             f"split across active voters\n")

    for t in TYPES:                                  # comparisons 1 & 2 (internal split)
        bp, vt, g = m[t]["BP"], m[t]["Voter"], m[t]["grand"]
        L.append(f"## {t}: BP vs all Voters")
        L.append(f"- Grand total distributed: **{g:,.2f}**")
        L.append("")
        L.append("| group | total | share % | recipients | mean/recipient |")
        L.append("|---|--:|--:|--:|--:|")
        L.append(f"| BP | {bp['total']:,.2f} | {bp['share']:.1f}% | {bp['count']:,} | {bp['mean']:,.2f} |")
        L.append(f"| Voter | {vt['total']:,.2f} | {vt['share']:.1f}% | {vt['count']:,} | {vt['mean']:,.2f} |")
        L.append("")
        L.append("```")
        L.append(f"BP    |{_bar(bp['share']):<22}| {bp['share']:.1f}%")
        L.append(f"Voter |{_bar(vt['share']):<22}| {vt['share']:.1f}%")
        L.append("```\n")

    for group in GROUPS:                             # comparisons 3 & 4 (cross-system)
        c = cross[group]
        L.append(f"## {group} rewards: DPoS vs PDDPoS")
        L.append(f"- DPoS **{c['dpos']:,.2f}** vs PDDPoS **{c['pddpos']:,.2f}**")
        L.append(f"- Difference: **{c['diff']:+,.2f}**  |  ratio DPoS/PDDPoS: "
                 f"**{c['ratio']:.3f}x**  |  change: **{c['pct']:+.1f}%**\n")

    L.append("## Takeaway")
    L.append(f"- PDDPoS routes **{m['PDDPoS']['Voter']['share']:.1f}%** of rewards to voters "
             f"vs DPoS **{m['DPoS']['Voter']['share']:.1f}%**.")
    L.append(f"- The DPoS producer keeps **{cross['BP']['pct']:+.1f}%** more than the PDDPoS "
             f"producer (self-vote pd=1 leaves a 'leftover'; PDDPoS self-vote pd=0 does not).")
    L.append("")

    text = "\n".join(L)
    with open(MD_OUT, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


def write_csv(m, cross):
    with open(CSV_OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["comparison", "type", "group", "total", "count", "share_pct",
                    "mean_per_recipient", "cross_diff", "cross_ratio", "cross_pct_change"])
        for t in TYPES:                              # internal splits
            for g in GROUPS:
                d = m[t][g]
                w.writerow([f"{t}: BP vs Voter", t, g, round(d["total"], 2), d["count"],
                            round(d["share"], 2), round(d["mean"], 2), "", "", ""])
        for group in GROUPS:                         # cross-system
            c = cross[group]
            w.writerow([f"{group}: DPoS vs PDDPoS", "DPoS", group, round(c["dpos"], 2),
                        "", "", "", round(c["diff"], 2), round(c["ratio"], 4), round(c["pct"], 2)])
            w.writerow([f"{group}: DPoS vs PDDPoS", "PDDPoS", group, round(c["pddpos"], 2),
                        "", "", "", round(c["diff"], 2), round(c["ratio"], 4), round(c["pct"], 2)])


# ===========================================================================
# Chart
# ===========================================================================
def _style(ax):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(BASE)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def _labels(ax, bars, fmt):
    for r in bars:
        ax.text(r.get_x() + r.get_width() / 2, r.get_height(), fmt(r.get_height()),
                ha="center", va="bottom", fontsize=8, color=INK)


def write_png(m):
    fig, (axA, axB) = plt.subplots(1, 2, figsize=(11, 4.6))
    fig.patch.set_facecolor(SURFACE)
    for ax in (axA, axB):
        _style(ax)

    # Panel A - grouped totals: groups [BP, Voter], series [PDDPoS, DPoS] (colour = system)
    x = range(len(GROUPS))
    w = 0.38
    pd_v = [m["PDDPoS"][g]["total"] for g in GROUPS]
    dp_v = [m["DPoS"][g]["total"] for g in GROUPS]
    bA1 = axA.bar([i - w / 2 for i in x], pd_v, w, label="PDDPoS",
                  color=C["PDDPoS"], edgecolor=SURFACE, linewidth=1.5)
    bA2 = axA.bar([i + w / 2 for i in x], dp_v, w, label="DPoS",
                  color=C["DPoS"], edgecolor=SURFACE, linewidth=1.5)
    axA.set_xticks(list(x))
    axA.set_xticklabels(["BP (producer)", "All voters"])
    axA.set_title("Reward totals: BP vs all voters", color=INK, fontsize=11, loc="left")
    axA.set_ylabel("Total reward (stake units)", color=INK2, fontsize=9)
    _labels(axA, bA1, lambda v: f"{v:,.0f}")
    _labels(axA, bA2, lambda v: f"{v:,.0f}")
    leg = axA.legend(frameon=False, fontsize=9, loc="upper left")
    for txt in leg.get_texts():
        txt.set_color(INK2)

    # Panel B - BP share of each system's block reward (colour = system)
    shares = [m[t]["BP"]["share"] for t in TYPES]
    bB = axB.bar(list(TYPES), shares, 0.55,
                 color=[C[t] for t in TYPES], edgecolor=SURFACE, linewidth=1.5)
    axB.set_ylim(0, 100)
    axB.set_title("BP share of block reward", color=INK, fontsize=11, loc="left")
    axB.set_ylabel("Producer share (%)", color=INK2, fontsize=9)
    for r, t in zip(bB, TYPES):
        axB.text(r.get_x() + r.get_width() / 2, r.get_height(), f"{r.get_height():.1f}%",
                 ha="center", va="bottom", fontsize=10, color=INK)
        axB.text(r.get_x() + r.get_width() / 2, 3, f"voters {100 - r.get_height():.1f}%",
                 ha="center", va="bottom", fontsize=8, color=SURFACE, weight="bold")

    fig.suptitle("PDDPoS vs DPoS - reward distribution", color=INK, fontsize=13,
                 x=0.02, ha="left", weight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(PNG_OUT, dpi=150, facecolor=SURFACE)
    plt.close(fig)


# ===========================================================================
def main():
    agg = load_totals()
    m, cross = build_metrics(agg)
    write_console_and_md(m, cross)
    write_csv(m, cross)
    write_png(m)
    print(f"\nSaved: {MD_OUT}\n       {CSV_OUT}\n       {PNG_OUT}")


if __name__ == "__main__":
    main()
