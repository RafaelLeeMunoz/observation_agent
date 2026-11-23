r"""Dev tool: append match adjudications (never edits existing entries).

Usage: .venv\Scripts\python tools\adjudicate.py <run_name> <verified_file> <json list of [obs_id, activity, decision, reason]>
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADJ = ROOT / "eval" / "benchmark" / "match_adjudications.json"


def main(argv):
    run_name, vf, items = argv[0], Path(argv[1]), json.loads(argv[2])
    obs = {o["obs_id"]: o for o in json.loads(vf.read_text(encoding="utf-8"))["observations"]}
    raw = ADJ.read_text(encoding="utf-8")
    adj = json.loads(raw)
    before = json.dumps(adj["entries"])
    n = len(adj["entries"])
    for oid, act, decision, reason in items:
        o = obs[oid]
        n += 1
        adj["entries"].append({"n": n, "when": "2026-11-23", "by": "claude (same credit rule)", "run": run_name,
                               "obs_id": oid, "activity": act, "msg_key": o["msg_key"], "task": o["task"],
                               "quote": o["quote"], "decision": decision, "reason": reason})
    assert json.dumps(adj["entries"][:len(json.loads(before))]) == before      # append-only
    ADJ.write_text(json.dumps(adj, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"appended {len(items)}; {n} entries")


if __name__ == "__main__":
    main(sys.argv[1:])
