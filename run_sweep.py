"""Committee-size sweep data generator for the PD-DPoS.

For each (m, k) config it runs the full pipeline fresh and archives the outputs,
because pddpos_engine.py / dpos_engine.py overwrite output/ each run:

    python pddpos_engine.py --clean 10 m k 128 0.5 1.5 1.0   (PD-DPoS, self pd=1)
    python dpos_engine.py                       (DPoS, appends type=DPoS rows)
    python analyze.py                           (BP-vs-voter reward split)
    

PD-DPoS runs at block-producer self-vote profit demand 1: the producer keeps the
fixed 20% production cost plus its own demand-weighted share of the 80% pool (the
reward split is (stake x pd) / sum(stake x pd)); voters split the remainder.

Configs: (m=10,k=20), (m=20,k=40), (m=30,k=60), (m=40,k=80)
  ->  committee m+k = 30, 60, 90, 120.
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # repo root
OUTPUT = os.path.join(ROOT, "output")
INPUTS = os.path.join(ROOT, "inputs")
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

CONFIGS = [(10, 20), (20, 40), (30, 60), (40, 80)]   # (m, k); n=10 fixed
N = 10

OUTPUT_FILES = ["rewards_dist.csv", "reward.csv", "blocks_produced.csv",
                "blocks_produced_dpos.csv", "node_ledger.csv", "node_ledger_dpos.csv",
                "downgrade_log.csv", "reward_analysis.csv", "reward_analysis.md",
                "summary.md", "summary_dpos.md"]
INPUT_FILES = ["vote_pool.csv", "BP_nodes_pd.csv", "CBP_nodes_pd.csv",
               "BP_nodes_dpos.csv", "CBP_nodes_dpos.csv", "election_info.csv"]


def run(cmd, stdin_text=None):
    print(f"  $ {' '.join(cmd)}")
    r = subprocess.run(cmd, cwd=ROOT, input=stdin_text, text=True,
                       capture_output=True)
    if r.returncode != 0:
        print(r.stdout[-2000:]); print(r.stderr[-2000:])
        raise SystemExit(f"command failed ({r.returncode}): {' '.join(cmd)}")
    return r.stdout


def archive(dst):
    os.makedirs(dst, exist_ok=True)
    for fn in OUTPUT_FILES:
        src = os.path.join(OUTPUT, fn)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dst, fn))
    for fn in INPUT_FILES:
        src = os.path.join(INPUTS, fn)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dst, fn))


def main():
    py = sys.executable
    os.makedirs(DATA, exist_ok=True)
    for m, k in CONFIGS:
        tag = f"m{m}_k{k}"
        print(f"\n=== config {tag}  (n={N}, m={m}, k={k}, committee m+k={m + k}) ===")
        run([py, "pddpos_engine.py", "--clean", str(N), str(m), str(k),
             "128", "0.5", "1.5", "1.0"])           # self pd=1: producer keeps 20% + margin
        run([py, "dpos_engine.py"])
        run([py, "analyze.py"])
        dst = os.path.join(DATA, tag)
        archive(dst)
        print(f"  archived -> {dst}")
    print("\nSweep complete. Archives under data/.")


if __name__ == "__main__":
    main()
