"""
One-off helper: re-run ONLY the profile call, to populate net_qualified.

The publications call is untouched and its stored results are reused, so
this does not redo the expensive half. Because Stage 3 runs at temperature 0
with a fixed seed, every pre-existing field should come back identical; the
script reports any field that changed, so a silent regression is visible.
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

    print(f"Refreshing profile call only, for {len(records)} record(s)\n")

    for path in records:
        stem = path.name.replace(".extracted.json", "")
        cv_text = (out_dir / f"{stem}.txt").read_text(encoding="utf-8")
        record = json.loads(path.read_text(encoding="utf-8"))
        old_profile = record.get("profile", {})

        call = ef.extract_profile(cv_text)
        if not call.ok:
            print(f"{stem}: FAILED - {call.error}")
            continue

        new_profile = call.data
        changed = []
        for key in set(old_profile) | set(new_profile):
            before = old_profile.get(key)
            after = new_profile.get(key)
            if json.dumps(before, sort_keys=True) != json.dumps(after, sort_keys=True):
                changed.append(key)

        elig = ef.extract_eligibility(cv_text)
        if elig.ok:
            new_profile["net_qualified"] = elig.data.get(
                "net_qualified", {"value": None, "evidence": ""})

        record["profile"] = new_profile
        record["provenance"]["profile_call"] = ef._provenance(call)
        record["provenance"]["eligibility_call"] = ef._provenance(elig)
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False),
                        encoding="utf-8")

        net = new_profile.get("net_qualified", {})
        net_value = net.get("value") if isinstance(net, dict) else None
        print(f"{stem}: net_qualified={net_value!s:<5} "
              f"changed fields: {sorted(changed) or 'none'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
