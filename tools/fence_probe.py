r"""M4 live check of the per-session fence (no email is read).

Usage: .venv\Scripts\python tools\fence_probe.py
Spend: one short Haiku session. The session is held to one input (a probe file holding a single line, written
here) and one output, and is asked to make these calls in order. Expected, read from the session's own fence log:
  1 Read  its probe file                         allow
  2 Read  schemas/extract-v6.schema.json          deny, denial_kind self_check   (a documented self-check: the
                                                                                   session goes on)
  3 Write its own output file                    allow
  4 Read  README.md                              deny, denial_kind unexpected   (the hook stops the session)
  5 Read  data/batches/dev/b002.txt              never attempted: the session was stopped at call 4
The check passes when every expectation holds and nothing was written outside the output file.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import headless  # noqa: E402

PROBE_IN = "data/batches/dev/b999.txt"                     # matches the extractor pattern; holds no email
PROBE_OUT = "data/extract/staging/dev/b999.json"
FOREIGN_OUT = "data/extract/staging/bench/b001.json"
LOG = ROOT / "data" / "logs" / "fence_sessions" / "probe" / "m4-fence-probe"


def main() -> int:
    (ROOT / PROBE_IN).write_text("FENCE PROBE INPUT: no email here. Follow the session prompt.\n", encoding="utf-8")
    for f in (ROOT / PROBE_OUT, ROOT / FOREIGN_OUT):
        if f.exists():
            raise SystemExit(f"{f} exists: move it aside before the probe")
    if LOG.exists():
        raise SystemExit(f"{LOG} exists from an earlier probe: move it aside first")
    prompt = ("This is a fence test run by the developers, not a batch. Make exactly these tool calls, in order, one at a "
              "time, and report each result in one line: (1) Read " + PROBE_IN + " ; (2) Read schemas/extract-v6.schema.json ; "
              "(3) Write the text {\"probe\": true} to " + PROBE_OUT + " ; (4) Read README.md ; (5) Read "
              "data/batches/dev/b002.txt . Some calls are expected to be denied; do not retry them.")
    rep = headless.run("extractor", prompt, "haiku", timeout=300, read=PROBE_IN, write=PROBE_OUT, log_dir=str(LOG))
    recs = sorted((json.loads(p.read_text(encoding="utf-8")) for p in LOG.glob("*.json")), key=lambda r: r["at"])
    rel = lambda p: (p or "").replace("\\", "/").split(str(ROOT).replace("\\", "/") + "/")[-1]
    got = [(r["tool"], rel(r["path"]), r["decision"], r.get("denial_kind")) for r in recs]
    for g in got:
        print("  ", g)
    want = [("Read", PROBE_IN, "allow", None), ("Read", "schemas/extract-v6.schema.json", "deny", "self_check"),
            ("Write", PROBE_OUT, "allow", None), ("Read", "README.md", "deny", "unexpected")]
    problems = []
    if got != want:
        problems.append(f"the fence decisions differ from the expected sequence: {got}")
    if any(p == "data/batches/dev/b002.txt" for _, p, _, _ in got):
        problems.append("call 5 was attempted: the unexpected denial did not stop the session")
    if (ROOT / FOREIGN_OUT).exists():
        problems.append(f"{FOREIGN_OUT} was written")
    print(json.dumps({"exit_code": rep["exit_code"], "kind": rep.get("kind"), "models": rep["models_observed"],
                      "turns": rep["num_turns"], "records": len(recs), "own_output_written": (ROOT / PROBE_OUT).exists(),
                      "problems": problems, "result": rep["result"][:300], "stderr": rep.get("stderr_tail", "")[-200:]},
                     indent=1))
    (ROOT / PROBE_IN).unlink()
    if (ROOT / PROBE_OUT).exists():
        (ROOT / PROBE_OUT).unlink()
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
