"""
One-off helper: re-extract positions section by section (defect 1).

Only the positions list is replaced. The profile prompt is NOT re-run,
because editing or re-running a working prompt is exactly what cost 10
points of accuracy in defect 4 - the other fields are left alone.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract_fields as ef  # noqa: E402


def main() -> int:
    out_dir = Path(__file__).resolve().parent.parent / "output"
    records = sorted(out_dir.glob("Candidate_*.extracted.json"))
    print(f"Re-extracting positions section by section, {len(records)} record(s)\n")

    for path in records:
        stem = path.name.replace(".extracted.json", "")
        cv_text = (out_dir / f"{stem}.txt").read_text(encoding="utf-8")
        record = json.loads(path.read_text(encoding="utf-8"))

        before = len(record.get("profile", {}).get("positions", []) or [])
        sections = ef.find_position_sections(cv_text)
        call = ef.extract_positions(cv_text)

        if not call.ok:
            print(f"{stem}: FAILED - {call.error}")
            continue

        positions = call.data.get("positions", [])
        record["profile"]["positions"] = positions
        record.setdefault("provenance", {})["positions_call"] = ef._provenance(call)
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False),
                        encoding="utf-8")

        headings = [h for h, _ in sections] or ["(whole CV fallback)"]
        print(f"{stem}: {before} -> {len(positions)} positions "
              f"from {len(headings)} section(s): {headings}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
