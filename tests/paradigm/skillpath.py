"""Where a test finds the code it covers.

The scripts live under apps/, because that is what publishes to a pod. The
tests live here, because nothing under a skill should reach a customer. So a
test cannot walk up to its own scripts and has to ask for them by skill name.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILLS = REPO / "apps" / "paradigm" / "skills"


def skill(name: str) -> Path:
    """The skill directory, e.g. skill("paradigm-options-recap")."""
    d = SKILLS / name
    if not d.is_dir():
        raise AssertionError(f"no skill at {d}")
    return d


def scripts(name: str) -> Path:
    return skill(name) / "scripts"
