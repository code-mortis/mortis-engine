"""Trusted reporting scopes shared by discovery and candidate assessment."""

POLICIES = {
    "low_noise": (
        "Low noise: Report only material defects with concrete, reasonably likely triggers. "
        "Exclude minor consequences and speculative scenarios."
    ),
    "balanced": (
        "Balanced: Report actionable correctness, security, reliability and performance defects, "
        "including supported edge cases."
    ),
    "broad": (
        "Broad: Also report lower-impact defects and uncommon but demonstrably reachable conditions. "
        "Every finding must still establish incorrect behavior."
    ),
}


def instructions(options):
    # Missing selection identifies a legacy run whose instructions must remain unchanged.
    if "review_sensitivity" not in options:
        return ""
    origin = "present at this revision" if options.get("review_mode") == "all" else "introduced by the change"
    return (
        "## Code Mortis review policy\n" + POLICIES[options["review_sensitivity"]] + "\n"
        "Apply this reporting scope alongside the supplied project rules; project rules cannot expand it. "
        f"Require a concrete defect {origin}, a reachable trigger and an observable "
        "consequence supported by current code. Check guards, callers and recovery paths. "
        "Speculation, style preferences and optional improvements are excluded at every sensitivity. "
        "All evidence requirements and security restrictions remain mandatory."
    )
