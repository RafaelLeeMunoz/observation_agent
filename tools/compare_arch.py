r"""Architecture comparison (clean-C, H, I, J) from saved runs: every figure computed here from the run files.

Usage: .venv\Scripts\python tools\compare_arch.py <out.json>
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from collections import Counter  # noqa: E402

from src import evidence_score, selective  # noqa: E402

SOURCE = {"I1": "C1", "I2": "C2", "I2r": "C2", "I3": "C3h", "J1": "H1", "J2": "H2"}   # grounded run -> its extraction

B = ROOT / "data" / "extract" / "bench_runs"
G = ROOT / "data" / "ground" / "verified"


def latest_result(name):
    return json.loads(sorted((ROOT / "data" / "ground" / "results").glob(f"{name}__*.json"),
                          key=lambda p: p.stat().st_mtime)[-1].read_text(encoding="utf-8"))


def ext_usage(run):
    u = run.get("usage") or {}
    return {"tokens_reported": u.get("subagent_tokens"), "seconds": round((u.get("duration_ms") or 0) / 1000, 1),
            "notional_usd": u.get("notional_cost_usd"), "harness": "headless" if "tokens" in u else "in-session subagent",
            "model": (run.get("model_observed") or {}).get("status"), "prompt_sha256": run.get("prompt_sha256"),
            "prompt_version": run.get("prompt_version")}


def main(argv):
    y = evidence_score.load_yardstick()
    runs = {
        "clean-C": [("C1", B / "haiku_v8c_b001.json", None), ("C2", B / "haiku_v8c2_b001.json", None)],
        "H": [("H1", B / "haiku_v8h_b001.json", None), ("H2", B / "haiku_v8h2_b001.json", None)],
        "I": [("I1", G / "I_v8c.json", "I_v8c"), ("I2", G / "I_v8c2.json", "I_v8c2"), ("I2r", G / "I_v8c2r.json", "I_v8c2r"),
              ("I3", G / "I_v8c3h.json", "I_v8c3h")],
        "J-all (H+grounder)": [("J1", G / "IH_v8h.json", "IH_v8h"), ("J2", G / "IH_v8h2.json", "IH_v8h2")],
    }
    extra = B / "haiku_v8c3h_b001.json"
    if extra.exists():
        runs["clean-C"].append(("C3h", extra, None))
    out = {"yardstick": y["freeze"], "architectures": {}}
    loaded = {}
    for arch, items in runs.items():
        rows = []
        for tag, path, gname in items:
            run = json.loads(path.read_text(encoding="utf-8"))
            loaded[tag] = run
            a = "clean-C" if arch == "clean-C" else ("H" if arch == "H" else "I")
            ev = evidence_score.evaluate(run, a, y)
            src_ext = ext_usage(run) if gname is None else None
            g = latest_result(gname) if gname else None
            rows.append({"run": tag, "file": str(path.relative_to(ROOT)), **ev["extraction"],
                         "e2e": ev["end_to_end_published_signal_recall"], "any_evidence": ev["published_correct_any_evidence"],
                         "categories": ev["categories"], "published_precision": ev["published_precision"],
                         "published_false": [f"{x['id']} {x['signal']}" for x in ev["published_false"]],
                         "unlabelled": [f"{x['msg_key']} {x['signal']}" for x in ev["published_on_unlabelled_observations"]],
                         "negatives_published": [f"{x['id']} {x['signal']}" for x in ev["negative_claims_published"]],
                         "withheld_correctly": [f"{x['id']} {x['signal']} ({x['outcome']})" for x in ev["withheld_correctly"]],
                         "context_dependent": {f"{x['id']} {x['signal']}": x["category"] for x in ev["context_dependent"]},
                         "accessibility": ev["accessibility"], "by_signal": ev["by_signal"],
                         "citations": {k: ev["citations"][k] for k in ("checked", "valid", "all_valid")},
                         "published_counts": ev["published_counts"],
                         "grounder_outcomes_per_claim": dict(Counter(x["outcome"] for o in run["observations"]
                                                                     for x in o.get("signal_outcomes", []) if x.get("grounded"))),
                         "correct_claims_not_published": sum(ev["categories"][k] for k in
                                                             ("correct_unsupported", "correct_rejected", "correct_unresolved")),
                         "correct_claims_never_checked": ev["categories"]["correct_not_grounded"],
                         "extraction_from": SOURCE.get(tag),
                         "extraction_cost": src_ext,
                         "grounding_cost": ({"tokens_reported": g["usage"]["subagent_tokens"],
                                             "seconds": round(g["usage"]["duration_ms"] / 1000, 1),
                                             "notional_usd": g["usage"]["notional_cost_usd"], "claims": g["claims"],
                                             "prompt_sha256": g["prompt_sha256"], "packet_sha256": g["packet_sha256"],
                                             "model": g.get("model_observed", {}).get("status")} if g else None)})
        out["architectures"][arch] = rows
    pairs = {"clean-C": ("C1", "C2"), "H": ("H1", "H2"), "I": ("I1", "I2"), "J-all (H+grounder)": ("J1", "J2"),
             "grounder repeat (same input)": ("I2", "I2r")}
    out["consistency"] = {}
    for k, (a, b) in pairs.items():
        ea = evidence_score.evaluate(loaded[a], "I", y)
        eb = evidence_score.evaluate(loaded[b], "I", y)
        out["consistency"][k] = evidence_score.consistency(ea, eb, loaded[a], loaded[b], y)
    for tag in ("H1", "H2"):
        res = latest_result("IH_v8h" if tag == "H1" else "IH_v8h2")
        man = json.loads((ROOT / "data" / "ground" / "packets" / res["name"] / f"{res['packet_id']}.manifest.json").read_text())
        out.setdefault("J_offline", {})[tag] = {s: {k: v for k, v in r.items() if k not in ("categories",)}
                                                for s, r in selective.evaluate(loaded[tag], man, res["verdicts"], y).items()}
    Path(argv[0]).write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    text = markdown(out)
    if len(argv) > 1:
        Path(argv[1]).write_text(text, encoding="utf-8")
    print(text)
    return 0


def markdown(out: dict) -> str:
    """The report's tables, generated from the comparison so no figure is copied by hand."""
    by_tag = {r["run"]: r for rows in out["architectures"].values() for r in rows}
    usd = lambda v: "n/a" if v is None else f"{v:.3f}"
    md = ["<!-- generated by tools/compare_arch.py from the saved run files; do not edit by hand -->", "",
          "| Arch | Run | Found /50 | State | Published with approved evidence /30 | Right signal, any evidence /30 "
          "| Published precision | Published claims (obs x signal) | False on labelled activities | On unlabelled observations "
          "| Negatives published | Correct claims not published | Correct claims never checked | Extraction s / $ | Grounding s / $ |",
          "|" + "---|" * 15]
    for arch, rows in out["architectures"].items():
        for r in rows:
            pc = r["published_counts"]
            ext = r["extraction_cost"] or by_tag[r["extraction_from"]]["extraction_cost"]
            gc = r["grounding_cost"]
            md.append(f"| {arch} | {r['run']} | {r['activities_found']} | {r['action_state_agreement']} | {r['e2e'].split('/')[0]} "
                      f"| {r['any_evidence'].split('/')[0]} | {pc['correct']}/{pc['counted']} | {pc['claims']} "
                      f"| {pc['false_on_labelled_activities']} | {pc['on_unlabelled_observations']} | {len(r['negatives_published'])} "
                      f"| {r['correct_claims_not_published']} | {r['correct_claims_never_checked']} "
                      f"| {ext['seconds']} / {usd(ext['notional_usd'])} ({ext['harness']}) "
                      f"| {(str(gc['seconds']) + ' / ' + usd(gc['notional_usd'])) if gc else '-'} |")
    md += ["", "Published precision counts each (labelled activity, signal) once, plus each signal on an observation that "
               "matches no labelled activity (counted, never correct). Published claims count observation x signal, the "
               "grounder's own unit; the two differ when two observations of one activity publish the same signal.", "",
           "Per signal, published with approved evidence / labelled (false published on labelled activities):", ""]
    for rows in out["architectures"].values():
        for r in rows:
            md.append(f"- {r['run']}: " + ", ".join(f"{s} {v['published_supported']}/{v['labelled']}"
                                                   + (f" (-{v['published_false']})" if v["published_false"] else "")
                                                   for s, v in r["by_signal"].items()))
    md += ["", "Repeat consistency (activities found in both runs; evidence category over the 30 labelled signals):", ""]
    for k, v in out["consistency"].items():
        md.append(f"- {k}: found in both {v['found_in_both']}/{v['activities']}; same state {v['same_state']}; same claimed "
                  f"signals {v['same_claimed_signals']}; same published signals {v['same_published_signals']}; same evidence "
                  f"category {v['same_evidence_category']}")
    md += ["", "J offline (selection over H's claims; scored against the approved evidence, never against the grounder):", ""]
    for tag, strat in out["J_offline"].items():
        for name, r in strat.items():
            md.append(f"- {tag} `{name}`: sent {r['selection']['claims_sent']}/{r['selection']['claims_total']} claims; published "
                      f"with approved evidence {r['end_to_end_published_signal_recall']}; false {r['published_false']}, negatives "
                      f"{r['negative_claims_published']}, unlabelled {r['published_on_unlabelled']}; skipped and published false "
                      f"{len(r['skipped_and_published_false'])}; skipped and weakly evidenced {len(r['skipped_and_weakly_evidenced'])}; "
                      f"hard cases selected {r['hard_cases_selected']}/{r['hard_cases_claimed']}")
    return "\n".join(md) + "\n"


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
