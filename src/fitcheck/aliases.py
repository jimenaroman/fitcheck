"""Skill aliases: merge spellings of the same skill so the gap tally counts it once (decision_rules.md §1).

The prompt already asks the model for canonical names; this table catches what drifts between calls.
Exact matches only. Add an entry when a real posting shows a split in the tally.
"""

SKILL_ALIASES = {
    "js": "javascript",
    "ts": "typescript",
    "py": "python",
    "k8s": "kubernetes",
    "postgres": "postgresql",
    "golang": "go",
    "ml": "machine learning",
    "google cloud platform": "gcp",
    "amazon web services": "aws",
    "node": "node.js",
    "nodejs": "node.js",
}


def canonical(name: str) -> str:
    return SKILL_ALIASES.get(name, name)


def canonicalize(names: list[str]) -> list[str]:
    """Map each name to its canonical form and drop repeats, keeping the first-seen order."""
    return list(dict.fromkeys(canonical(n) for n in names))


def names_for(skill: str) -> list[str]:
    """The canonical name plus every variant that maps to it: "javascript" -> ["javascript", "js"]."""
    return [skill] + [variant for variant, name in SKILL_ALIASES.items() if name == skill]
