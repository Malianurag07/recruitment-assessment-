"""Maps skill variants to one canonical name using the skill_aliases table (no LLM needed)."""
from app.database import get_connection


def load_aliases(conn=None) -> dict[str, str]:
    """Load the alias table. Pass a connection to reuse one (tests, or inside a transaction)."""
    if conn is not None:
        return {r["alias"]: r["canonical"] for r in conn.execute("SELECT alias, canonical FROM skill_aliases")}
    with get_connection() as own:
        return load_aliases(own)


def normalize_skill(skill: str, aliases: dict[str, str]) -> str:
    skill = skill.strip()
    return aliases.get(skill.lower(), skill)


def normalize_skills(skills: list[str], aliases: dict[str, str]) -> list[tuple[str, str]]:
    """Return [(canonical, raw)] with duplicates (by canonical, case-insensitive) removed."""
    seen, out = set(), []
    for raw in skills:
        canon = normalize_skill(raw, aliases)
        if canon.lower() not in seen:
            seen.add(canon.lower())
            out.append((canon, raw))
    return out
