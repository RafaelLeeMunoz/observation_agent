r"""Make a clean copy of the working tree for a true cold run (user, 24 Nov 2026), without touching the working tree or its baseline outputs.

Usage: .venv\Scripts\python tools\make_cold_copy.py C:\Users\Xigua\GitHub\observation_agent_cold

Copies exactly what a fresh checkout of the current working tree would hold: every file git tracks or would add (uncommitted changes
included), minus the run state (`data/`, `output/`, `private/`, `eval/results/`). The corpus is copied byte for byte. `.venv` is a directory
junction to this repository's virtual environment (a fresh clone would build its own), so the copy needs no second install. Refuses to write
into a non-empty folder. Prints a manifest hash of the copied files so the copy can be compared with the working tree.
"""
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP = ("data/", "output/", "private/", "eval/results/", ".venv/", ".git/", "notebooks/.ipynb_checkpoints/")


def tracked_names() -> list[str]:
    files = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return sorted({n.decode("utf-8") for n in files.split(b"\0") if n and not n.decode("utf-8").startswith(SKIP) and not n.decode("utf-8").endswith(".pyc")
                   and (ROOT / n.decode("utf-8")).is_file()})


def verify(dest: Path) -> list[str]:
    """Differences between a copy and the current working tree (empty = the copy holds exactly what a checkout of the tree would, and no run state).
    Documentation (`docs/` and the root `*.md` files) may be edited after the copy is made without making it stale: the benchmark's code, configuration,
    prompts, schemas, tests, tools and corpus may not."""
    out = []
    for name in tracked_names():
        if name.startswith("docs/") or ("/" not in name and name.endswith(".md")):
            continue
        p = dest / name
        if not p.is_file():
            out.append(f"missing in the copy: {name}")
        elif p.read_bytes() != (ROOT / name).read_bytes():
            out.append(f"differs: {name}")
    for state in ("data", "output", "private"):
        if (dest / state).exists():
            out.append(f"the copy already has {state}/: it is not cold")
    return out


def main(argv=None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] == "--verify":
        diffs = verify(Path(args[1]).resolve())
        print(f"{len(diffs)} differences between the copy and the working tree" + ("" if not diffs else ": " + "; ".join(diffs[:10])))
        return 1 if diffs else 0
    dest = Path(args[0]).resolve()
    if dest.exists() and any(dest.iterdir()):
        raise SystemExit(f"{dest} is not empty: refusing")
    if ROOT in dest.parents or dest == ROOT:
        raise SystemExit("the copy must live outside the working tree")
    files = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    names = sorted({n.decode("utf-8") for n in files.split(b"\0") if n})
    h, n_copied = hashlib.sha256(), 0
    for name in names:
        if name.startswith(SKIP) or name.endswith(".pyc"):
            continue
        src = ROOT / name
        if not src.is_file():
            continue
        out = dest / name
        out.parent.mkdir(parents=True, exist_ok=True)
        data = src.read_bytes()
        out.write_bytes(data)
        h.update(name.encode() + b"\0" + hashlib.sha256(data).digest())
        n_copied += 1
    link = dest / ".venv"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(ROOT / ".venv")], check=True, capture_output=True)
    print(f"copied {n_copied} files to {dest}; manifest sha256 {h.hexdigest()[:16]}; .venv -> {ROOT / '.venv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
