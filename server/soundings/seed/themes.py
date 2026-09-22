"""Seed the initial observation themes into ``catalogue.theme``.

Run via ``soundings.seed.run`` (called early, before any observation-
dependent seeds).  Idempotent: uses ``INSERT ... ON CONFLICT (key) DO
NOTHING`` so re-running is safe.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

# (key, label, description) for the 12 initial observation themes.
THEMES: tuple[tuple[str, str, str], ...] = (
    ("housing", "Housing", "Housing affordability homelessness tenancy issues housing conditions."),
    ("health", "Health", "Physical health access to healthcare health outcomes."),
    ("mental_health", "Mental Health", "Mental health needs access to mental health services."),
    ("employment", "Employment", "Employment unemployment workforce participation job quality."),
    ("education", "Education", "Educational attainment school readiness adult education."),
    ("crime", "Crime & Safety", "Crime anti-social behaviour perceptions of safety."),
    ("food_insecurity", "Food Insecurity", "Access to food food bank usage food poverty."),
    ("debt", "Debt & Financial Exclusion", "Personal debt access to credit financial exclusion."),
    (
        "immigration_asylum",
        "Immigration & Asylum",
        "Needs of migrants asylum seekers and refugees.",
    ),
    (
        "digital_exclusion",
        "Digital Exclusion",
        "Lack of internet access digital skills device access.",
    ),
    ("social_isolation", "Social Isolation", "Loneliness social isolation community connection."),
    (
        "climate_environment",
        "Climate & Environment",
        "Environmental quality climate impacts green space.",
    ),
)

_INSERT_SQL = text(
    "INSERT INTO catalogue.theme (key, label, description) "
    "VALUES (:key, :label, :description) "
    "ON CONFLICT (key) DO NOTHING"
)


async def seed_themes(engine: AsyncEngine) -> None:
    """Insert the initial 12 themes (idempotent via ON CONFLICT DO NOTHING)."""
    async with engine.begin() as conn:
        for key, label, description in THEMES:
            await conn.execute(
                _INSERT_SQL,
                {"key": key, "label": label, "description": description},
            )
    print(f"[seed] themes: {len(THEMES)} rows")
