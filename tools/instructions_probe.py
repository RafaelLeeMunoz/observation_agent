r"""M4 2b live check: a fenced worker loads no project instruction files (two short Haiku sessions, no email is read).

Usage: .venv\Scripts\python tools\instructions_probe.py
Re-run it after every Claude Code update: the exclusion is a CLI setting, and this is the proof it still works.

It plants two harmless files in the project (CLAUDE.local.md and .claude/rules/zz_instr_probe.md, removed afterwards) so
that all three kinds of instruction file exist beside the real CLAUDE.md, then launches the same worker command twice:
  control  src.headless.command(isolate=False): must load CLAUDE.md, CLAUDE.local.md and the rules file (a positive control:
           without it, "nothing loaded" could just mean the hook never fires)
  shipped  src.headless.run(...), the real launch: must load none of them
Both read each session's InstructionsLoaded records (metadata only). Passes only when both hold.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import headless  # noqa: E402

LOG = ROOT / "data" / "logs" / "fence_sessions" / "probe" / "m4-instructions-probe"
PLANTED = {ROOT / "CLAUDE.local.md": "# Planted by instructions_probe.py\nNot a real instruction.\n",
           ROOT / ".claude" / "rules" / "zz_instr_probe.md": "# Planted by instructions_probe.py\nNot a real instruction.\n"}
PROMPT = "Configuration check from the developers. Use no tools and read no file. Reply with the single word OK."
EXPECTED = {"CLAUDE.md", "CLAUDE.local.md", ".claude/rules/zz_instr_probe.md"}


def loaded_names(log_dir: Path) -> set[str]:
    return {Path(x["file_path"]).relative_to(ROOT).as_posix() if Path(x["file_path"]).is_absolute() else x["file_path"]
            for x in headless.instructions_loaded(log_dir)}


def main() -> int:
    if os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is set: refusing (CLAUDE.md rule 3)")
    if LOG.exists():
        raise SystemExit(f"{LOG} exists from an earlier probe: move it aside first")
    for f in PLANTED:
        if f.exists():
            raise SystemExit(f"{f} exists: move it aside before the probe")
    try:
        for f, text in PLANTED.items():
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text, encoding="utf-8")
        cmd = headless.command("extractor", PROMPT, "haiku", log_dir=str(LOG / "control"), isolate=False)
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(ROOT)}
        proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, timeout=300)
        rep = headless.run("extractor", PROMPT, "haiku", timeout=300, log_dir=str(LOG / "shipped"))
    finally:
        for f in PLANTED:
            if f.exists():
                f.unlink()
        rules = ROOT / ".claude" / "rules"
        if rules.is_dir() and not any(rules.iterdir()):
            rules.rmdir()
    control, shipped = loaded_names(LOG / "control"), loaded_names(LOG / "shipped")
    problems = []
    if proc.returncode != 0 or rep["exit_code"] != 0:
        problems.append(f"a session failed (control exit {proc.returncode}, shipped exit {rep['exit_code']})")
    if control != EXPECTED:
        problems.append(f"the control did not load exactly {sorted(EXPECTED)}: {sorted(control)}")
    if shipped:
        problems.append(f"the shipped launch loaded instruction files: {sorted(shipped)}")
    print(json.dumps({"control_loaded": sorted(control), "shipped_loaded": sorted(shipped), "models": rep["models_observed"],
                      "planted_removed": not any(f.exists() for f in PLANTED), "problems": problems}, indent=1))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
