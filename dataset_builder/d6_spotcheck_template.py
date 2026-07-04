"""D6 — Human spot-check template (spec section 6 D6, Step 8).

Emits a template for a manual audit of ~8 selected records. A human fills in
pass/fail per record checking: gold answer follows from evidence; evidence page is
valid; bucket label reasonable; expected inputs reasonable; unit/period clear.

Output: data/spotcheck.json  e.g.
    {"sample_size": 8, "passed": 7, "failed": 1, "pass_rate": 0.875,
     "notes": "One question excluded due to ambiguous period."}

The pass rate goes in the pitch.

TODO(Step 8): read data/subset.json, sample ~8 demo_candidate items, write a
pre-filled template to data/spotcheck_template.json for a human to complete.
"""

from __future__ import annotations


def main() -> int:
    raise NotImplementedError("d6 spotcheck: implement in Step 8 (spec section 6 D6).")


if __name__ == "__main__":
    raise SystemExit(main())
