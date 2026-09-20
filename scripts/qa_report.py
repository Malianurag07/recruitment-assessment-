"""Turn docs/qa_results/*.json into the case tables of docs/TEST_REPORT.md (between the markers).

Run:  python scripts/qa_report.py
The narrative parts of the report (findings, architecture analysis) are written by hand around the generated tables.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "docs" / "qa_results"
REPORT = ROOT / "docs" / "TEST_REPORT.md"
START, END = "<!-- CASES:START -->", "<!-- CASES:END -->"


def esc(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")[:260]


def build() -> str:
    cases = []
    for f in ("offline", "live"):
        p = RES / f"{f}.json"
        if p.exists():
            cases += json.loads(p.read_text(encoding="utf-8"))["cases"]
    out, scen = [], {}
    for c in cases:
        scen.setdefault(c["scenario"], []).append(c)
    out.append("| Scenario | Cases | Pass | Fail |\n|---|---|---|---|")
    for s, cs in scen.items():
        out.append(f"| {s} | {len(cs)} | {sum(c['passed'] for c in cs)} | {sum(not c['passed'] for c in cs)} |")
    out.append(f"| **Total** | **{len(cases)}** | **{sum(c['passed'] for c in cases)}** | **{sum(not c['passed'] for c in cases)}** |\n")
    for s, cs in scen.items():
        out.append(f"### {s}\n\n| ID | Case | Expected | Result | What happened |\n|---|---|---|---|---|")
        for c in cs:
            out.append(f"| {c['id']} | {esc(c['title'])} | {esc(c['expected'])} | {'PASS' if c['passed'] else '**FAIL**'} | {esc(c['actual'])} |")
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    block = f"{START}\n{build()}\n{END}"
    text = REPORT.read_text(encoding="utf-8") if REPORT.exists() else f"# Test report\n\n{START}\n{END}\n"
    if START in text:
        text = text[: text.index(START)] + block + text[text.index(END) + len(END):]
    else:
        text += "\n" + block
    REPORT.write_text(text, encoding="utf-8")
    print("wrote", REPORT)
