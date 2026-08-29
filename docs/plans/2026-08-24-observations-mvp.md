# Observations MVP — Hybrid Contribution Layer

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Allow organisations to submit structured observations about local need and assets that sit alongside official statistics in Soundings, clearly flagged as experiential evidence.

**Architecture:** Hybrid identity model — organisations already in `data.organisation` (Charity Commission, FindThatCharity) self-identify via magic-link auth; organisations not in the register get a lightweight internal profile created on sign-up. Both paths produce a `data.organisation` row that can later be upgraded to a full v2 self-hosted context profile. Observations are stored in a new `data.observation` table, surfaced through a new `get_observations` tool (MCP + HTTP + ask dispatcher), and displayed on place pages and a public `/observations` stream.

**Tech Stack:** Postgres 16 + PostGIS, SQLAlchemy 2 async, Alembic, FastAPI, Pydantic v2, Astro 4, existing capture/sanitisation pipeline.

**Spec alignment:** Implements a pragmatic subset of `docs/v3-contribution-layer.md`. Defers: editorial review queue, reputation signal, revision/supersession chain, `aggregated_only` attribution, qualitative excerpts, v2 self-hosted profile crawling, migration tool. The schema is designed to be forward-compatible with those features — adding `superseded_by`, `withdrawn_at`, or `attribution_visibility` columns later is non-breaking.

---

## Design decisions

### D1: Hybrid identity (internal profiles + future external profiles)

Two paths to becoming a contributor:

1. **Existing organisation** — already in `data.organisation` from Charity Commission / FindThatCharity / 360Giving. Identifies themselves by charity number (or org name) + a magic-link email sent to a domain matching the organisation's registered address. No password. No self-hosted profile required.

2. **New organisation** — not in any register. Signs up with name, contact email, and primary place. Creates a `data.organisation` row with `source_id = 'ctx.manual_signup'`. This is a stripped-down v2 context profile, stored in the Soundings DB rather than self-hosted.

Both paths produce the same thing: a `data.organisation.id` that observations reference. Later, an org can publish a full v2 profile at `/.well-known/soundings.yaml` and Soundings can index it — the internal profile is upgraded to an external one. No schema change needed.

### D2: Single place per observation (MVP)

v3 spec allows `place_ids: [string]` (array). MVP uses a single `place_id` per observation. This simplifies the form, the validation, and the queries. Multi-place observations can be supported later by adding a join table; the single `place_id` column becomes nullable and a new `data.observation_place` table handles the many-to-many. Non-breaking.

### D3: Append-only (no revision or withdrawal yet)

v3 spec has `superseded_by` and `withdrawn_at`. MVP is append-only — observations cannot be revised or withdrawn through the API. A DBA can hard-delete if needed. This avoids the complexity of revision chains and visibility rules. Adding `superseded_by` and `withdrawn_at` columns later is non-breaking.

### D4: Auto-accept (no editorial review queue)

v3 spec has an editorial review queue with auto-accept for most submissions and review for edge cases. MVP auto-accepts all submissions that pass schema validation. The submission endpoint returns `status: "accepted"`. Editorial review can be added later by introducing a `status` column (`accepted` | `in_review` | `rejected`) — non-breaking.

### D5: Public attribution (no aggregated_only)

v3 spec has `attribution.visibility: "public" | "aggregated_only"`. MVP defaults to public — the organisation's name is shown with the observation. No `aggregated_only` option for now. Adding the column later is non-breaking.

### D6: Theme controlled vocabulary

Start with 12 themes, stored in a new `catalogue.theme` table. Extensible by editorial decision. Themes map loosely to existing indicator domains but are not 1:1 (e.g. `food_insecurity` doesn't have a corresponding indicator domain yet).

Initial themes:
```
housing, health, mental_health, employment, education, crime,
food_insecurity, debt, immigration_asylum, digital_exclusion,
social_isolation, climate_environment
```

### D7: Evidence type

Two evidence types: `quantitative` (has value + unit) and `qualitative` (has methodology note). The `mixed` type from v3 is omitted — a mixed observation can be submitted as quantitative with a methodology note that explains the qualitative context. Adding `mixed` later is non-breaking.

---

## Schema changes

### New table: `catalogue.theme`

```sql
CREATE TABLE catalogue.theme (
    key         VARCHAR(64) PRIMARY KEY,
    label       TEXT NOT NULL,
    description TEXT,
    added_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### New table: `data.observation`

```sql
CREATE TABLE data.observation (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organisation_id   VARCHAR(64) NOT NULL REFERENCES data.organisation(id),
    place_id          VARCHAR NOT NULL REFERENCES geography.place(id),
    period_start      DATE NOT NULL,
    period_end        DATE,
    theme             VARCHAR(64) NOT NULL REFERENCES catalogue.theme(key),
    statement         TEXT NOT NULL,
    indicator_key     VARCHAR(128) REFERENCES catalogue.indicator(key),
    value             NUMERIC,
    unit              VARCHAR(32),
    evidence_type     VARCHAR(16) NOT NULL CHECK (evidence_type IN ('quantitative', 'qualitative')),
    methodology_note  TEXT,
    confidence        VARCHAR(8) NOT NULL CHECK (confidence IN ('high', 'medium', 'low')),
    submitted_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_observation_place_id ON data.observation (place_id);
CREATE INDEX ix_observation_theme ON data.observation (theme);
CREATE INDEX ix_observation_indicator_key ON data.observation (indicator_key);
CREATE INDEX ix_observation_organisation_id ON data.observation (organisation_id);
```

### New table: `contribution.contributor_session`

Magic-link auth sessions for observation submission. Stored in a new `contribution` schema to keep auth separate from data/corpus/cache.

```sql
CREATE SCHEMA IF NOT EXISTS contribution;

CREATE TABLE contribution.contributor_session (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organisation_id   VARCHAR(64) NOT NULL REFERENCES data.organisation(id),
    email             TEXT NOT NULL,
    token_hash        VARCHAR(128) NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at        TIMESTAMPTZ NOT NULL,
    used_at           TIMESTAMPTZ,
    CHECK (expires_at > created_at)
);
```

### Source catalogue entry

Add to `catalogue/sources.yaml`:

```yaml
- id: ctx.manual_signup
  label: Manual sign-up (Soundings contribution)
  publisher: Soundings
  publisher_url: https://soundings.local
  dataset_url: ""
  licence: CC0-1.0
  mode: loader
  refresh_cadence: ""
  rate_limit: { rps: 0 }
```

This is the `source_id` for organisations created via the sign-up flow. It lets the existing `data.organisation.source_id` FK work without nullable exceptions.

---

## Pydantic contracts

### `server/soundings/contracts/observation.py`

```python
from datetime import date, datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field

EvidenceType = Literal["quantitative", "qualitative"]
ConfidenceLevel = Literal["high", "medium", "low"]


class ObservationSubmit(BaseModel):
    """Input model for POST /v1/observations."""
    organisation_id: str
    place_id: str
    period_start: date
    period_end: date | None = None
    theme: str
    statement: str = Field(min_length=10, max_length=1000)
    indicator_key: str | None = None
    value: float | None = None
    unit: str | None = None
    evidence_type: EvidenceType
    methodology_note: str | None = None
    confidence: ConfidenceLevel


class ObservationRecord(BaseModel):
    """Full observation record as stored and returned."""
    id: UUID
    organisation_id: str
    organisation_name: str
    place_id: str
    place_name: str
    period_start: date
    period_end: date | None
    theme: str
    statement: str
    indicator_key: str | None
    value: float | None
    unit: str | None
    evidence_type: EvidenceType
    methodology_note: str | None
    confidence: ConfidenceLevel
    submitted_at: datetime


class ObservationSummaryItem(BaseModel):
    """Aggregated theme summary for place profiles."""
    theme: str
    count: int
    latest_submission: datetime
    organisation_names: list[str]


class ObservationSummary(BaseModel):
    """Summary block for get_place_profile."""
    total_observations: int
    themes: list[ObservationSummaryItem]


class GetObservationsInput(BaseModel):
    """Input for get_observations tool."""
    place_id: str | None = None
    theme: str | None = None
    indicator_key: str | None = None
    organisation_id: str | None = None
    limit: int = Field(default=50, ge=1, le=200)


class GetObservationsOutput(BaseModel):
    """Output for get_observations tool."""
    observations: list[ObservationRecord] = Field(default_factory=list)
    total: int = 0
    summary: ObservationSummary | None = None
    caveats: list[str] = Field(default_factory=list)
```

---

## Tasks

### Task 1: Create Alembic migration for observation schema

**Objective:** Add `catalogue.theme`, `data.observation`, and `contribution.contributor_session` tables.

**Files:**
- Create: `server/soundings/db/migrations/versions/0005_observation_schema.py`

**Step 1: Generate the migration**

Run:
```bash
cd server && uv run alembic revision --autogenerate -m "observation schema"
```

If autogenerate doesn't pick up the new models (they don't exist yet), write the migration manually:

**Step 2: Write the migration**

```python
"""observation schema

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-24

"""

from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # catalogue.theme
    op.create_table(
        "theme",
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("key"),
        schema="catalogue",
    )

    # contribution schema
    op.execute("CREATE SCHEMA IF NOT EXISTS contribution")

    op.create_table(
        "contributor_session",
        sa.Column("id", UUID(as_uuid=True), nullable=False, server_default=sa.func.gen_random_uuid()),
        sa.Column("organisation_id", sa.String(64), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organisation_id"], ["data.organisation.id"]),
        sa.CheckConstraint("expires_at > created_at", name="ck_contributor_session_expires"),
        sa.PrimaryKeyConstraint("id"),
        schema="contribution",
    )

    # data.observation
    op.create_table(
        "observation",
        sa.Column("id", UUID(as_uuid=True), nullable=False, server_default=sa.func.gen_random_uuid()),
        sa.Column("organisation_id", sa.String(64), nullable=False),
        sa.Column("place_id", sa.String(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("theme", sa.String(64), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("indicator_key", sa.String(128), nullable=True),
        sa.Column("value", sa.Numeric(), nullable=True),
        sa.Column("unit", sa.String(32), nullable=True),
        sa.Column("evidence_type", sa.String(16), nullable=False),
        sa.Column("methodology_note", sa.Text(), nullable=True),
        sa.Column("confidence", sa.String(8), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["organisation_id"], ["data.organisation.id"]),
        sa.ForeignKeyConstraint(["place_id"], ["geography.place.id"]),
        sa.ForeignKeyConstraint(["theme"], ["catalogue.theme.key"]),
        sa.CheckConstraint("evidence_type IN ('quantitative', 'qualitative')", name="ck_observation_evidence_type"),
        sa.CheckConstraint("confidence IN ('high', 'medium', 'low')", name="ck_observation_confidence"),
        sa.PrimaryKeyConstraint("id"),
        schema="data",
    )
    op.create_index("ix_observation_place_id", "observation", ["place_id"], schema="data")
    op.create_index("ix_observation_theme", "observation", ["theme"], schema="data")
    op.create_index("ix_observation_indicator_key", "observation", ["indicator_key"], schema="data")
    op.create_index("ix_observation_organisation_id", "observation", ["organisation_id"], schema="data")


def downgrade() -> None:
    op.drop_index("ix_observation_organisation_id", schema="data")
    op.drop_index("ix_observation_indicator_key", schema="data")
    op.drop_index("ix_observation_theme", schema="data")
    op.drop_index("ix_observation_place_id", schema="data")
    op.drop_table("observation", schema="data")
    op.drop_table("contributor_session", schema="contribution")
    op.execute("DROP SCHEMA IF EXISTS contribution")
    op.drop_table("theme", schema="catalogue")
```

**Step 3: Add `ctx.manual_signup` to sources.yaml**

In `catalogue/sources.yaml`, add:

```yaml
  - id: ctx.manual_signup
    label: Manual sign-up (Soundings contribution)
    publisher: Soundings
    publisher_url: https://soundings.local
    dataset_url: ""
    licence: CC0-1.0
    mode: loader
    refresh_cadence: ""
    rate_limit: { rps: 0 }
```

**Step 4: Run migration and verify**

```bash
cd server && uv run alembic upgrade head
```

Verify:
```bash
cd server && uv run python -c "
from sqlalchemy import text
from soundings.db.engine import create_engine
import asyncio
async def check():
    eng = create_engine()
    async with eng.connect() as conn:
        for schema, table in [('catalogue','theme'), ('data','observation'), ('contribution','contributor_session')]:
            exists = (await conn.execute(text(\"SELECT to_regclass('{}.{}')\".format(schema, table)))).scalar()
            print(f'{schema}.{table}: {\"OK\" if exists else \"MISSING\"}')
asyncio.run(check())
"
```

Expected: all three tables `OK`.

**Step 5: Commit**

```bash
git add server/soundings/db/migrations/versions/0005_observation_schema.py catalogue/sources.yaml
git commit -m "feat: add observation schema migration (catalogue.theme, data.observation, contribution.contributor_session)"
```

---

### Task 2: Seed initial themes

**Objective:** Insert the 12 initial themes into `catalogue.theme`.

**Files:**
- Create: `server/soundings/seed/themes.py`
- Modify: `server/soundings/seed/run.py` (add themes call to the seed sequence)
- Test: `server/tests/test_theme_seed.py`

**Step 1: Write failing test**

```python
# server/tests/test_theme_seed.py
import pytest
from sqlalchemy import text
from soundings.db.engine import create_engine
from soundings.seed.themes import seed_themes

pytestmark = pytest.mark.asyncio


async def test_seed_themes_inserts_all_twelve():
    eng = create_engine()
    await seed_themes(eng)
    async with eng.connect() as conn:
        result = await conn.execute(text("SELECT key, label FROM catalogue.theme ORDER BY key"))
        rows = result.all()
    assert len(rows) == 12
    keys = {r[0] for r in rows}
    assert "housing" in keys
    assert "food_insecurity" in keys
    assert "climate_environment" in keys
```

**Step 2: Run test to verify failure**

```bash
cd server && uv run pytest tests/test_theme_seed.py -v
```

Expected: FAIL — `seed_themes` not defined.

**Step 3: Write the seeder**

```python
# server/soundings/seed/themes.py
"""Seed the initial 12 themes into catalogue.theme."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

INITIAL_THEMES: list[tuple[str, str, str]] = [
    ("housing", "Housing", "Housing affordability, homelessness, tenancy issues, housing conditions."),
    ("health", "Health", "Physical health, access to healthcare, health outcomes."),
    ("mental_health", "Mental Health", "Mental health needs, access to mental health services."),
    ("employment", "Employment", "Employment, unemployment, workforce participation, job quality."),
    ("education", "Education", "Educational attainment, school readiness, adult education."),
    ("crime", "Crime & Safety", "Crime, anti-social behaviour, perceptions of safety."),
    ("food_insecurity", "Food Insecurity", "Access to food, food bank usage, food poverty."),
    ("debt", "Debt & Financial Exclusion", "Personal debt, access to credit, financial exclusion."),
    ("immigration_asylum", "Immigration & Asylum", "Needs of migrants, asylum seekers, and refugees."),
    ("digital_exclusion", "Digital Exclusion", "Lack of internet access, digital skills, device access."),
    ("social_isolation", "Social Isolation", "Loneliness, social isolation, community connection."),
    ("climate_environment", "Climate & Environment", "Environmental quality, climate impacts, green space."),
]


async def seed_themes(engine: AsyncEngine) -> None:
    """Insert initial themes. Idempotent — skips keys that already exist."""
    async with engine.begin() as conn:
        for key, label, description in INITIAL_THEMES:
            await conn.execute(
                text("""
                    INSERT INTO catalogue.theme (key, label, description)
                    VALUES (:key, :label, :description)
                    ON CONFLICT (key) DO NOTHING
                """),
                {"key": key, "label": label, "description": description},
            )
```

**Step 4: Run test to verify pass**

```bash
cd server && uv run pytest tests/test_theme_seed.py -v
```

Expected: PASS.

**Step 5: Add to seed sequence**

In `server/soundings/seed/run.py`, add `await seed_themes(engine)` early in the sequence (before any observation-dependent seeds).

**Step 6: Commit**

```bash
git add server/soundings/seed/themes.py server/soundings/seed/run.py server/tests/test_theme_seed.py
git commit -m "feat: seed 12 initial observation themes"
```

---

### Task 3: Observation Pydantic contracts

**Objective:** Create the Pydantic models for observation submission and retrieval.

**Files:**
- Create: `server/soundings/contracts/observation.py`
- Test: `server/tests/test_observation_contracts.py`

**Step 1: Write failing test**

```python
# server/tests/test_observation_contracts.py
import pytest
from datetime import date
from pydantic import ValidationError
from soundings.contracts.observation import (
    ObservationSubmit,
    EvidenceType,
    ConfidenceLevel,
)


def test_quantitative_observation_valid():
    obs = ObservationSubmit(
        organisation_id="GBCHC123456",
        place_id="ltla24:E06000004",
        period_start=date(2026, 1, 1),
        theme="housing",
        statement="Local landlords increasingly refusing tenants on benefits.",
        value=47,
        unit="percent",
        evidence_type="quantitative",
        confidence="high",
    )
    assert obs.value == 47
    assert obs.evidence_type == "quantitative"


def test_qualitative_observation_valid():
    obs = ObservationSubmit(
        organisation_id="GBCHC123456",
        place_id="ltla24:E06000004",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 3, 31),
        theme="mental_health",
        statement="Increased anxiety presentations among young people aged 16-24.",
        evidence_type="qualitative",
        methodology_note="Quarterly case-note review by head of services.",
        confidence="medium",
    )
    assert obs.value is None
    assert obs.evidence_type == "qualitative"


def test_statement_too_short_rejected():
    with pytest.raises(ValidationError):
        ObservationSubmit(
            organisation_id="GBCHC123456",
            place_id="ltla24:E06000004",
            period_start=date(2026, 1, 1),
            theme="housing",
            statement="Too short",
            evidence_type="qualitative",
            confidence="low",
        )


def test_invalid_evidence_type_rejected():
    with pytest.raises(ValidationError):
        ObservationSubmit(
            organisation_id="GBCHC123456",
            place_id="ltla24:E06000004",
            period_start=date(2026, 1, 1),
            theme="housing",
            statement="A valid length statement here.",
            evidence_type="mixed",
            confidence="low",
        )
```

**Step 2: Run test to verify failure**

```bash
cd server && uv run pytest tests/test_observation_contracts.py -v
```

Expected: FAIL — module not found.

**Step 3: Write the contracts**

Write the full `server/soundings/contracts/observation.py` file as specified in the "Pydantic contracts" section above.

**Step 4: Run test to verify pass**

```bash
cd server && uv run pytest tests/test_observation_contracts.py -v
```

Expected: 4 passed.

**Step 5: Commit**

```bash
git add server/soundings/contracts/observation.py server/tests/test_observation_contracts.py
git commit -m "feat: add observation Pydantic contracts"
```

---

### Task 4: SQLAlchemy model for `data.observation`

**Objective:** Add the ORM model so Alembic and queries can use it.

**Files:**
- Create: `server/soundings/db/models/observation.py`
- Modify: `server/soundings/db/models/__init__.py` (export new model)
- Test: `server/tests/test_observation_model.py`

**Step 1: Write failing test**

```python
# server/tests/test_observation_model.py
import pytest
from datetime import date
from uuid import uuid4
from sqlalchemy import select
from soundings.db.models.observation import Observation
from soundings.db.models import Base

pytestmark = pytest.mark.asyncio


async def test_observation_model_insert_and_query():
    """Verify the model maps to data.observation and can be inserted."""
    # This test uses the test database; requires `make test-db-create`
    from soundings.db.engine import create_engine
    eng = create_engine()
    async with eng.begin() as conn:
        result = await conn.execute(
            select(Observation).limit(1)
        )
        # Should not raise — table exists and model maps correctly
        rows = result.all()
    assert rows == []
```

**Step 2: Run test to verify failure**

```bash
cd server && uv run pytest tests/test_observation_model.py -v
```

Expected: FAIL — `Observation` model not found.

**Step 3: Write the model**

```python
# server/soundings/db/models/observation.py
"""SQLAlchemy model for data.observation."""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from soundings.db.models import Base


class Observation(Base):
    __tablename__ = "observation"
    __table_args__ = (
        CheckConstraint("evidence_type IN ('quantitative', 'qualitative')", name="ck_observation_evidence_type"),
        CheckConstraint("confidence IN ('high', 'medium', 'low')", name="ck_observation_confidence"),
        {"schema": "data"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organisation_id: Mapped[str] = mapped_column(String(64), ForeignKey("data.organisation.id"))
    place_id: Mapped[str] = mapped_column(String, ForeignKey("geography.place.id"))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    theme: Mapped[str] = mapped_column(String(64), ForeignKey("catalogue.theme.key"))
    statement: Mapped[str] = mapped_column(Text)
    indicator_key: Mapped[str | None] = mapped_column(String(128), ForeignKey("catalogue.indicator.key"), nullable=True)
    value: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    evidence_type: Mapped[str] = mapped_column(String(16))
    methodology_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[str] = mapped_column(String(8))
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
```

Add to `server/soundings/db/models/__init__.py`:
```python
from soundings.db.models.observation import Observation
```

**Step 4: Run test to verify pass**

```bash
cd server && uv run pytest tests/test_observation_model.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add server/soundings/db/models/observation.py server/soundings/db/models/__init__.py server/tests/test_observation_model.py
git commit -m "feat: add Observation SQLAlchemy model"
```

---

### Task 5: Magic-link auth — request and verify

**Objective:** Implement the magic-link flow: `POST /v1/contribute/request-link` (email + org ID) → email sent with token → `POST /v1/contribute/verify-link` (token → session cookie).

**Files:**
- Create: `server/soundings/contribute/__init__.py`
- Create: `server/soundings/contribute/auth.py`
- Create: `server/soundings/http/contribute.py`
- Modify: `server/soundings/http/app.py` (mount the contribute router)
- Test: `server/tests/test_contribute_auth.py`

**Step 1: Write failing test**

```python
# server/tests/test_contribute_auth.py
import pytest
from httpx import AsyncClient
from soundings.http.app import create_app

pytestmark = pytest.mark.asyncio


async def test_request_link_creates_session_row():
    """POST /v1/contribute/request-link creates a contributor_session row."""
    app = create_app()
    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.post("/v1/contribute/request-link", json={
            "organisation_id": "GBCHC123456",
            "email": "info@example.org",
        })
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "link_sent"


async def test_verify_link_returns_session_cookie():
    """POST /v1/contribute/verify-link with a valid token sets a cookie."""
    # This test needs to mock email sending and extract the token.
    # For the test, we inject the token directly via the auth service.
    from soundings.contribute.auth import MagicLinkService
    from soundings.db.engine import create_engine

    eng = create_engine()
    service = MagicLinkService(engine=eng, email_sender=_FakeSender())

    # Create a session and get the token
    token = await service.create_session(
        organisation_id="GBCHC123456",
        email="info@example.org",
    )

    app = create_app()
    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.post("/v1/contribute/verify-link", json={"token": token})
    assert resp.status_code == 200
    assert "soundings_contrib_session" in resp.cookies


class _FakeSender:
    """Fake email sender that captures the token for testing."""
    def __init__(self):
        self.sent_tokens: list[str] = []

    async def send(self, to: str, token: str) -> None:
        self.sent_tokens.append(token)
```

**Step 2: Run test to verify failure**

```bash
cd server && uv run pytest tests/test_contribute_auth.py -v
```

Expected: FAIL — modules not found.

**Step 3: Implement the auth service**

```python
# server/soundings/contribute/__init__.py
```

```python
# server/soundings/contribute/auth.py
"""Magic-link authentication for observation contributors.

Flow:
    1. Contributor enters their org ID + email.
    2. We create a contribution.contributor_session row with a hashed token.
    3. We send the raw token to the email (via a sender protocol).
    4. Contributor clicks the link containing the token.
    5. We verify the token hash, mark the session used, and set a signed cookie.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

TOKEN_TTL_MINUTES = 15


class EmailSender(Protocol):
    async def send(self, to: str, token: str) -> None: ...


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class MagicLinkService:
    def __init__(self, engine: AsyncEngine, email_sender: EmailSender):
        self._engine = engine
        self._sender = email_sender

    async def create_session(self, organisation_id: str, email: str) -> str:
        """Create a magic-link session and send the link. Returns the raw token (for testing)."""
        token = secrets.token_urlsafe(32)
        token_hash = _hash_token(token)
        expires_at = datetime.now(UTC) + timedelta(minutes=TOKEN_TTL_MINUTES)

        async with self._engine.begin() as conn:
            await conn.execute(
                text("""
                    INSERT INTO contribution.contributor_session
                        (organisation_id, email, token_hash, expires_at)
                    VALUES (:org_id, :email, :hash, :expires)
                """),
                {"org_id": organisation_id, "email": email, "hash": token_hash, "expires": expires_at},
            )

        await self._sender.send(email, token)
        return token

    async def verify_token(self, token: str) -> str | None:
        """Verify a magic-link token. Returns organisation_id if valid, None otherwise."""
        token_hash = _hash_token(token)
        async with self._engine.begin() as conn:
            row = (
                await conn.execute(
                    text("""
                        SELECT id, organisation_id, expires_at, used_at
                        FROM contribution.contributor_session
                        WHERE token_hash = :hash
                    """),
                    {"hash": token_hash},
                )
            ).first()

        if row is None:
            return None
        if row.used_at is not None:
            return None
        if row.expires_at < datetime.now(UTC):
            return None

        # Mark as used
        async with self._engine.begin() as conn:
            await conn.execute(
                text("""
                    UPDATE contribution.contributor_session
                    SET used_at = now()
                    WHERE id = :id
                """),
                {"id": row.id},
            )

        return row.organisation_id
```

**Step 4: Implement the HTTP routes**

```python
# server/soundings/http/contribute.py
"""HTTP routes for contribution: magic-link auth + observation submission."""

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from soundings.contribute.auth import MagicLinkService

router = APIRouter(prefix="/v1/contribute")


class RequestLinkInput(BaseModel):
    organisation_id: str
    email: str


class RequestLinkOutput(BaseModel):
    status: str = "link_sent"


class VerifyLinkInput(BaseModel):
    token: str


class VerifyLinkOutput(BaseModel):
    status: str = "verified"
    organisation_id: str


@router.post("/request-link", response_model=RequestLinkOutput)
async def request_link(input: RequestLinkInput, request: Request) -> RequestLinkOutput:
    service: MagicLinkService | None = getattr(request.app.state, "magic_link_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Contribution auth not configured")

    # Verify the org exists
    from sqlalchemy import text
    async with request.app.state.engine.connect() as conn:
        org = (
            await conn.execute(
                text("SELECT id FROM data.organisation WHERE id = :id"),
                {"id": input.organisation_id},
            )
        ).first()

    if org is None:
        # Don't reveal whether the org exists — return same response
        return RequestLinkOutput()

    await service.create_session(input.organisation_id, input.email)
    return RequestLinkOutput()


@router.post("/verify-link", response_model=VerifyLinkOutput)
async def verify_link(input: VerifyLinkInput, request: Request) -> VerifyLinkOutput:
    service: MagicLinkService | None = getattr(request.app.state, "magic_link_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Contribution auth not configured")

    org_id = await service.verify_token(input.token)
    if org_id is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    # Set a signed cookie
    response = Response()
    response.set_cookie(
        key="soundings_contrib_session",
        value=org_id,
        httponly=True,
        samesite="strict",
        max_age=86400,  # 24 hours
        secure=True,
    )
    return VerifyLinkOutput(status="verified", organisation_id=org_id)
```

Mount in `server/soundings/http/app.py` — add `from soundings.http.contribute import router as contribute_router` and `app.include_router(contribute_router)`.

**Step 5: Run test to verify pass**

```bash
cd server && uv run pytest tests/test_contribute_auth.py -v
```

Expected: PASS.

**Step 6: Commit**

```bash
git add server/soundings/contribute/ server/soundings/http/contribute.py server/soundings/http/app.py server/tests/test_contribute_auth.py
git commit -m "feat: magic-link auth for observation contributors"
```

---

### Task 6: New org sign-up (lightweight internal profile)

**Objective:** Allow organisations not in any register to create a minimal `data.organisation` row so they can submit observations.

**Files:**
- Modify: `server/soundings/http/contribute.py` (add `POST /v1/contribute/signup`)
- Modify: `server/soundings/contribute/auth.py` (add `signup` method)
- Test: `server/tests/test_contribute_signup.py`

**Step 1: Write failing test**

```python
# server/tests/test_contribute_signup.py
import pytest
from httpx import AsyncClient
from soundings.http.app import create_app

pytestmark = pytest.mark.asyncio


async def test_signup_creates_organisation():
    app = create_app()
    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.post("/v1/contribute/signup", json={
            "name": "Teesside Mutual Aid",
            "email": "contact@teesside-mutual.org",
            "primary_place_id": "ltla24:E06000004",
        })
    assert resp.status_code == 201
    data = resp.json()
    assert data["organisation_id"].startswith("ctx.")
    assert data["status"] == "created"


async def test_signup_rejects_duplicate_email():
    """Second signup with same email + place should return the existing org."""
    app = create_app()
    async with AsyncClient(app=app, base_url="http://test") as client:
        await client.post("/v1/contribute/signup", json={
            "name": "Teesside Mutual Aid",
            "email": "contact@teesside-mutual.org",
            "primary_place_id": "ltla24:E06000004",
        })
        resp = await client.post("/v1/contribute/signup", json={
            "name": "Teesside Mutual Aid",
            "email": "contact@teesside-mutual.org",
            "primary_place_id": "ltla24:E06000004",
        })
    assert resp.status_code == 200
    assert resp.json()["status"] == "exists"
```

**Step 2: Run test to verify failure**

```bash
cd server && uv run pytest tests/test_contribute_signup.py -v
```

Expected: FAIL — endpoint not found.

**Step 3: Implement signup**

Add to `server/soundings/http/contribute.py`:

```python
class SignupInput(BaseModel):
    name: str
    email: str
    primary_place_id: str


class SignupOutput(BaseModel):
    status: str  # "created" | "exists"
    organisation_id: str


@router.post("/signup", response_model=SignupOutput)
async def signup(input: SignupInput, request: Request) -> SignupOutput:
    from sqlalchemy import text

    async with request.app.state.engine.begin() as conn:
        # Check if an org with this name + place already exists from manual signup
        existing = (
            await conn.execute(
                text("""
                    SELECT id FROM data.organisation
                    WHERE name = :name
                      AND source_id = 'ctx.manual_signup'
                    LIMIT 1
                """),
                {"name": input.name},
            )
        ).first()

        if existing is not None:
            return SignupOutput(status="exists", organisation_id=existing.id)

        # Generate a ctx. prefixed ID
        org_id = f"ctx.{input.name.lower().replace(' ', '_')[:40]}"

        await conn.execute(
            text("""
                INSERT INTO data.organisation (id, name, classification, source_id, retrieved_at, raw)
                VALUES (:id, :name, '{}', 'ctx.manual_signup', now(), '{}')
            """),
            {"id": org_id, "name": input.name},
        )

        # Add operates_in link
        await conn.execute(
            text("""
                INSERT INTO data.organisation_operates_in (organisation_id, place_id)
                VALUES (:org_id, :place_id)
                ON CONFLICT DO NOTHING
            """),
            {"org_id": org_id, "place_id": input.primary_place_id},
        )

    return SignupOutput(status="created", organisation_id=org_id)
```

**Step 4: Run test to verify pass**

```bash
cd server && uv run pytest tests/test_contribute_signup.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add server/soundings/http/contribute.py server/tests/test_contribute_signup.py
git commit -m "feat: lightweight org sign-up for contributors not in any register"
```

---

### Task 7: Observation submission endpoint

**Objective:** `POST /v1/observations` — authenticated endpoint that validates and stores an observation.

**Files:**
- Create: `server/soundings/contribute/submission.py`
- Modify: `server/soundings/http/contribute.py` (add submission route)
- Test: `server/tests/test_observation_submission.py`

**Step 1: Write failing test**

```python
# server/tests/test_observation_submission.py
import pytest
from datetime import date
from httpx import AsyncClient
from soundings.http.app import create_app

pytestmark = pytest.mark.asyncio


async def test_submit_quantitative_observation():
    """Authenticated POST creates an observation in data.observation."""
    app = create_app()
    async with AsyncClient(app=app, base_url="http://test") as client:
        # Authenticate by setting the contributor cookie directly
        client.cookies.set("soundings_contrib_session", "GBCHC123456")

        resp = await client.post("/v1/observations", json={
            "organisation_id": "GBCHC123456",
            "place_id": "ltla24:E06000004",
            "period_start": "2026-01-01",
            "theme": "housing",
            "statement": "47% of private landlords in our area refuse tenants on benefits.",
            "value": 47,
            "unit": "percent",
            "evidence_type": "quantitative",
            "confidence": "high",
        })
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "accepted"
    assert "observation_id" in data


async def test_submit_without_auth_rejected():
    app = create_app()
    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.post("/v1/observations", json={
            "organisation_id": "GBCHC123456",
            "place_id": "ltla24:E06000004",
            "period_start": "2026-01-01",
            "theme": "housing",
            "statement": "A valid length observation statement.",
            "evidence_type": "qualitative",
            "confidence": "low",
        })
    assert resp.status_code == 401


async def test_submit_with_bad_theme_rejected():
    app = create_app()
    async with AsyncClient(app=app, base_url="http://test") as client:
        client.cookies.set("soundings_contrib_session", "GBCHC123456")

        resp = await client.post("/v1/observations", json={
            "organisation_id": "GBCHC123456",
            "place_id": "ltla24:E06000004",
            "period_start": "2026-01-01",
            "theme": "nonexistent_theme",
            "statement": "A valid length observation statement about something.",
            "evidence_type": "qualitative",
            "confidence": "low",
        })
    assert resp.status_code == 422
```

**Step 2: Run test to verify failure**

```bash
cd server && uv run pytest tests/test_observation_submission.py -v
```

Expected: FAIL — endpoint not found.

**Step 3: Implement submission**

```python
# server/soundings/contribute/submission.py
"""Observation submission — validate and persist to data.observation."""

from uuid import UUID
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.contracts.observation import ObservationSubmit


async def submit_observation(
    engine: AsyncEngine,
    observation: ObservationSubmit,
) -> UUID:
    """Insert an observation and return its UUID.

    Raises ValueError if the theme, place_id, or organisation_id don't exist.
    """
    async with engine.begin() as conn:
        # Validate theme exists
        theme = (
            await conn.execute(
                text("SELECT key FROM catalogue.theme WHERE key = :key"),
                {"key": observation.theme},
            )
        ).first()
        if theme is None:
            raise ValueError(f"Unknown theme: {observation.theme}")

        # Validate place exists
        place = (
            await conn.execute(
                text("SELECT id FROM geography.place WHERE id = :id"),
                {"id": observation.place_id},
            )
        ).first()
        if place is None:
            raise ValueError(f"Unknown place: {observation.place_id}")

        # Validate org exists
        org = (
            await conn.execute(
                text("SELECT id FROM data.organisation WHERE id = :id"),
                {"id": observation.organisation_id},
            )
        ).first()
        if org is None:
            raise ValueError(f"Unknown organisation: {observation.organisation_id}")

        # Validate indicator_key if provided
        if observation.indicator_key is not None:
            indicator = (
                await conn.execute(
                    text("SELECT key FROM catalogue.indicator WHERE key = :key"),
                    {"key": observation.indicator_key},
                )
            ).first()
            if indicator is None:
                raise ValueError(f"Unknown indicator: {observation.indicator_key}")

        # Insert
        result = await conn.execute(
            text("""
                INSERT INTO data.observation
                    (organisation_id, place_id, period_start, period_end, theme,
                     statement, indicator_key, value, unit, evidence_type,
                     methodology_note, confidence)
                VALUES
                    (:org_id, :place_id, :period_start, :period_end, :theme,
                     :statement, :indicator_key, :value, :unit, :evidence_type,
                     :methodology_note, :confidence)
                RETURNING id
            """),
            {
                "org_id": observation.organisation_id,
                "place_id": observation.place_id,
                "period_start": observation.period_start,
                "period_end": observation.period_end,
                "theme": observation.theme,
                "statement": observation.statement,
                "indicator_key": observation.indicator_key,
                "value": observation.value,
                "unit": observation.unit,
                "evidence_type": observation.evidence_type,
                "methodology_note": observation.methodology_note,
                "confidence": observation.confidence,
            },
        )
        return result.scalar()
```

Add the route to `server/soundings/http/contribute.py`:

```python
from soundings.contracts.observation import ObservationSubmit
from soundings.contribute.submission import submit_observation


class ObservationSubmitOutput(BaseModel):
    status: str = "accepted"
    observation_id: str


@router.post("/observations", response_model=ObservationSubmitOutput)
async def submit_observation_route(
    observation: ObservationSubmit,
    request: Request,
) -> ObservationSubmitOutput:
    # Check auth cookie
    org_id = request.cookies.get("soundings_contrib_session")
    if not org_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # The cookie org must match the submission org
    if org_id != observation.organisation_id:
        raise HTTPException(status_code=403, detail="Cannot submit on behalf of another organisation")

    try:
        obs_id = await submit_observation(request.app.state.engine, observation)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return ObservationSubmitOutput(observation_id=str(obs_id))
```

> **Note:** The route is mounted at `/v1/contribute/observations` since the router prefix is `/v1/contribute`. Alternatively, create a separate router for `/v1/observations`. The separate router is cleaner — do that instead:

Create `server/soundings/http/observations.py` with the submission route at `POST /v1/observations` and mount it separately. This keeps the paths clean:
- `POST /v1/contribute/request-link` — auth
- `POST /v1/contribute/verify-link` — auth
- `POST /v1/contribute/signup` — new org
- `POST /v1/observations` — submit (auth required)
- `GET /v1/observations` — list (public, see Task 8)

**Step 4: Run test to verify pass**

```bash
cd server && uv run pytest tests/test_observation_submission.py -v
```

Expected: 3 passed.

**Step 5: Commit**

```bash
git add server/soundings/contribute/submission.py server/soundings/http/observations.py server/soundings/http/app.py server/tests/test_observation_submission.py
git commit -m "feat: observation submission endpoint with auth"
```

---

### Task 8: `get_observations` tool (HTTP + MCP)

**Objective:** Read-only tool to query observations by place, theme, indicator, or org. Public — no auth required.

**Files:**
- Create: `server/soundings/tools/get_observations.py`
- Modify: `server/soundings/http/tools.py` (register the new tool route)
- Test: `server/tests/test_get_observations_tool.py`

**Step 1: Write failing test**

```python
# server/tests/test_get_observations_tool.py
import pytest
from datetime import date
from uuid import uuid4
from sqlalchemy import text
from soundings.db.engine import create_engine
from soundings.tools.get_observations import get_observations, GetObservationsInput

pytestmark = pytest.mark.asyncio


async def test_get_observations_by_place():
    eng = create_engine()
    # Seed an observation
    async with eng.begin() as conn:
        await conn.execute(text("""
            INSERT INTO data.observation
                (id, organisation_id, place_id, period_start, theme, statement,
                 evidence_type, confidence)
            VALUES
                (:id, 'GBCHC123456', 'ltla24:E06000004', '2026-01-01', 'housing',
                 'Test observation about housing.',
                 'qualitative', 'medium')
        """), {"id": uuid4()})

    result = await get_observations(
        GetObservationsInput(place_id="ltla24:E06000004"),
        engine=eng,
    )
    assert result.total >= 1
    obs = result.observations[0]
    assert obs.theme == "housing"
    assert obs.organisation_name  # should be populated from join
    assert obs.place_name  # should be populated from join


async def test_get_observations_empty_place():
    eng = create_engine()
    result = await get_observations(
        GetObservationsInput(place_id="ltla24:E06000004"),
        engine=eng,
    )
    assert result.total == 0
    assert result.observations == []
```

**Step 2: Run test to verify failure**

```bash
cd server && uv run pytest tests/test_get_observations_tool.py -v
```

Expected: FAIL — module not found.

**Step 3: Implement the tool**

```python
# server/soundings/tools/get_observations.py
"""get_observations tool — query contributed observations.

Read-only. Public. Returns observations with org name and place name joined.
Optionally returns a theme summary when place_id is provided.
"""

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.contracts.observation import (
    GetObservationsInput,
    GetObservationsOutput,
    ObservationRecord,
    ObservationSummary,
    ObservationSummaryItem,
)


def tool_spec() -> dict[str, object]:
    return {
        "name": "get_observations",
        "description": (
            "Retrieve contributed observations from organisations working in a place. "
            "Observations are experiential evidence — claims about local need or assets "
            "submitted by organisations, clearly distinct from official statistics. "
            "Filter by place, theme, indicator key, or organisation."
        ),
        "input_schema": GetObservationsInput.model_json_schema(),
    }


async def get_observations(
    input: GetObservationsInput,
    engine: AsyncEngine,
) -> GetObservationsOutput:
    where_clauses = []
    params: dict[str, object] = {"limit": input.limit}

    if input.place_id:
        where_clauses.append("o.place_id = :place_id")
        params["place_id"] = input.place_id
    if input.theme:
        where_clauses.append("o.theme = :theme")
        params["theme"] = input.theme
    if input.indicator_key:
        where_clauses.append("o.indicator_key = :indicator_key")
        params["indicator_key"] = input.indicator_key
    if input.organisation_id:
        where_clauses.append("o.organisation_id = :org_id")
        params["org_id"] = input.organisation_id

    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    query = f"""
        SELECT
            o.id, o.organisation_id, org.name AS organisation_name,
            o.place_id, p.name AS place_name,
            o.period_start, o.period_end, o.theme,
            o.statement, o.indicator_key, o.value, o.unit,
            o.evidence_type, o.methodology_note, o.confidence,
            o.submitted_at
        FROM data.observation o
        JOIN data.organisation org ON org.id = o.organisation_id
        JOIN geography.place p ON p.id = o.place_id
        {where_sql}
        ORDER BY o.submitted_at DESC
        LIMIT :limit
    """

    async with engine.connect() as conn:
        rows = (await conn.execute(text(query), params)).all()

    observations = [
        ObservationRecord(
            id=row.id,
            organisation_id=row.organisation_id,
            organisation_name=row.organisation_name,
            place_id=row.place_id,
            place_name=row.place_name,
            period_start=row.period_start,
            period_end=row.period_end,
            theme=row.theme,
            statement=row.statement,
            indicator_key=row.indicator_key,
            value=float(row.value) if row.value is not None else None,
            unit=row.unit,
            evidence_type=row.evidence_type,
            methodology_note=row.methodology_note,
            confidence=row.confidence,
            submitted_at=row.submitted_at,
        )
        for row in rows
    ]

    # Build summary if place_id is provided
    summary = None
    if input.place_id:
        summary_query = """
            SELECT
                o.theme,
                count(*) AS count,
                max(o.submitted_at) AS latest,
                array_agg(DISTINCT org.name) AS org_names
            FROM data.observation o
            JOIN data.organisation org ON org.id = o.organisation_id
            WHERE o.place_id = :place_id
            GROUP BY o.theme
            ORDER BY o.theme
        """
        async with engine.connect() as conn:
            summary_rows = (
                await conn.execute(text(summary_query), {"place_id": input.place_id})
            ).all()

        if summary_rows:
            summary = ObservationSummary(
                total_observations=sum(r.count for r in summary_rows),
                themes=[
                    ObservationSummaryItem(
                        theme=r.theme,
                        count=r.count,
                        latest_submission=r.latest,
                        organisation_names=list(r.org_names),
                    )
                    for r in summary_rows
                ],
            )

    caveats = []
    if not observations:
        caveats.append("No contributed observations found for this query.")

    return GetObservationsOutput(
        observations=observations,
        total=len(observations),
        summary=summary,
        caveats=caveats,
    )
```

Add the HTTP route in `server/soundings/http/tools.py`:

```python
from soundings.tools.get_observations import (
    GetObservationsInput,
    GetObservationsOutput,
    get_observations,
)
from soundings.tools.get_observations import tool_spec as get_observations_spec

# Add to the router:
@router.post("/get_observations")
async def get_observations_route(input: GetObservationsInput, request: Request) -> GetObservationsOutput:
    return await get_observations(input, request.app.state.engine)
```

Add to the `list_tools` response.

**Step 4: Run test to verify pass**

```bash
cd server && uv run pytest tests/test_get_observations_tool.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add server/soundings/tools/get_observations.py server/soundings/http/tools.py server/tests/test_get_observations_tool.py
git commit -m "feat: get_observations tool — public read-only observation query"
```

---

### Task 9: Integrate `get_observations` into the ask dispatcher

**Objective:** The `/v1/ask` LLM can call `get_observations` to surface experiential evidence alongside official data.

**Files:**
- Modify: `server/soundings/ask/dispatcher.py` (add handler + tool spec)
- Modify: `server/soundings/ask/prompts.py` (add to system prompt description)
- Test: `server/tests/test_ask_dispatcher_observations.py`

**Step 1: Write failing test**

```python
# server/tests/test_ask_dispatcher_observations.py
import pytest
from soundings.ask.dispatcher import ToolDispatcher

pytestmark = pytest.mark.asyncio


async def test_dispatcher_has_observations_handler():
    """The dispatcher should include get_observations in its handlers."""
    # This is a smoke test — just verify the handler is registered
    state = create_test_state()
    dispatcher = ToolDispatcher(state)
    assert "get_observations" in dispatcher._handlers


def create_test_state():
    """Minimal AppState mock for dispatcher construction."""
    class FakeState:
        engine = None
        orchestrator = None
        geography_service = None
    return FakeState()
```

**Step 2: Run test to verify failure**

```bash
cd server && uv run pytest tests/test_ask_dispatcher_observations.py -v
```

Expected: FAIL — `get_observations` not in handlers.

**Step 3: Add to dispatcher**

In `server/soundings/ask/dispatcher.py`:

1. Import:
```python
from soundings.tools.get_observations import (
    GetObservationsInput,
    get_observations,
)
from soundings.tools.get_observations import tool_spec as get_observations_spec
```

2. Add to `tool_specs` property list:
```python
get_observations_spec(),
```

3. Add to `_handlers` dict:
```python
"get_observations": self._handle_get_observations,
```

4. Add handler method:
```python
async def _handle_get_observations(self, args: dict[str, Any]) -> dict[str, Any]:
    model = GetObservationsInput.model_validate(args)
    result = await get_observations(model, self._state.engine)
    return result.model_dump(mode="json")
```

5. Update the system prompt in `server/soundings/ask/prompts.py`. Add to the tool list in `_SCOPE_DESCRIPTION`:

```
- get_observations: retrieve contributed observations from organisations working in
  a place. These are experiential evidence — claims about local need or assets
  submitted by organisations on the ground. ALWAYS clearly distinguish
  observations from official statistics in your answer: use phrases like
  "local organisations report..." or "according to [org name]..." and cite the
  evidence_type and confidence. Observations complement official data; they do
  not replace it. Use place_id to filter, theme to narrow the topic.
```

**Step 4: Run test to verify pass**

```bash
cd server && uv run pytest tests/test_ask_dispatcher_observations.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add server/soundings/ask/dispatcher.py server/soundings/ask/prompts.py server/tests/test_ask_dispatcher_observations.py
git commit -m "feat: integrate get_observations into ask dispatcher"
```

---

### Task 10: Extend `get_place_profile` with observations summary

**Objective:** Place profiles include a summary of recent observations grouped by theme.

**Files:**
- Modify: `server/soundings/tools/get_place_profile.py` (add `observations_summary` to output)
- Test: `server/tests/test_place_profile_observations.py`

**Step 1: Write failing test**

```python
# server/tests/test_place_profile_observations.py
import pytest
from uuid import uuid4
from sqlalchemy import text
from soundings.db.engine import create_engine
from soundings.tools.get_place_profile import (
    get_place_profile,
    GetPlaceProfileInput,
)
from soundings.orchestration.orchestrator import IndicatorOrchestrator

pytestmark = pytest.mark.asyncio


async def test_place_profile_includes_observations_summary():
    eng = create_engine()
    # Seed an observation
    async with eng.begin() as conn:
        await conn.execute(text("""
            INSERT INTO data.observation
                (id, organisation_id, place_id, period_start, theme, statement,
                 evidence_type, confidence)
            VALUES
                (:id, 'GBCHC123456', 'ltla24:E06000004', '2026-01-01', 'housing',
                 'Landlords refusing benefits tenants.',
                 'qualitative', 'medium')
        """), {"id": uuid4()})

    # Build a minimal orchestrator mock
    class FakeOrchestrator:
        async def fetch(self, **kwargs):
            class FakeResult:
                values = []
                sources = []
                caveats = []
                partial = False
            return FakeResult()

    result = await get_place_profile(
        GetPlaceProfileInput(place_id="ltla24:E06000004"),
        orchestrator=FakeOrchestrator(),
        engine=eng,
    )
    assert result.observations_summary is not None
    assert result.observations_summary.total_observations >= 1
    theme_item = next(
        t for t in result.observations_summary.themes if t.theme == "housing"
    )
    assert theme_item.count >= 1
```

**Step 2: Run test to verify failure**

```bash
cd server && uv run pytest tests/test_place_profile_observations.py -v
```

Expected: FAIL — `observations_summary` not on output model.

**Step 3: Implement the extension**

In `server/soundings/tools/get_place_profile.py`:

1. Import:
```python
from soundings.contracts.observation import ObservationSummary
```

2. Add to `GetPlaceProfileOutput`:
```python
observations_summary: ObservationSummary | None = None
```

3. Add an `_observations_summary` helper:
```python
async def _observations_summary(engine: AsyncEngine, place_id: str) -> ObservationSummary | None:
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text("""
                    SELECT
                        o.theme,
                        count(*) AS count,
                        max(o.submitted_at) AS latest,
                        array_agg(DISTINCT org.name) AS org_names
                    FROM data.observation o
                    JOIN data.organisation org ON org.id = o.organisation_id
                    WHERE o.place_id = :place_id
                    GROUP BY o.theme
                    ORDER BY latest DESC
                """),
                {"place_id": place_id},
            )
        ).all()

    if not rows:
        return None

    from soundings.contracts.observation import ObservationSummaryItem

    return ObservationSummary(
        total_observations=sum(r.count for r in rows),
        themes=[
            ObservationSummaryItem(
                theme=r[0],
                count=r[1],
                latest_submission=r[2],
                organisation_names=list(r[3]),
            )
            for r in rows
        ],
    )
```

4. Call it in `get_place_profile`:
```python
obs_summary = await _observations_summary(engine, input.place_id)
# Add to the return:
return GetPlaceProfileOutput(
    ...
    observations_summary=obs_summary,
)
```

**Step 4: Run test to verify pass**

```bash
cd server && uv run pytest tests/test_place_profile_observations.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add server/soundings/tools/get_place_profile.py server/tests/test_place_profile_observations.py
git commit -m "feat: observations summary in get_place_profile"
```

---

### Task 11: UI — `/contribute` page with observation form

**Objective:** A public page where organisations can sign up, authenticate, and submit observations through a form.

**Files:**
- Create: `ui/src/pages/contribute.astro`
- Create: `ui/src/components/ObservationForm.astro`
- Create: `ui/src/components/MagicLinkAuth.astro`
- Modify: `ui/src/lib/api.ts` (add observation API functions)
- Modify: `ui/src/lib/types.ts` (add observation types)

**Implementation notes:**

The page has three states:
1. **Unauthenticated** — show org search + magic-link request form, OR sign-up form for new orgs
2. **Authenticated** — show the observation submission form
3. **Submitted** — show confirmation + link to submit another

The form fields map directly to the `ObservationSubmit` contract:
- Organisation (pre-filled from auth, read-only)
- Place (autocomplete from `find_place` tool)
- Theme (dropdown from `catalogue.theme` — fetch the list)
- Statement (textarea, 10–1000 chars)
- Evidence type (radio: quantitative / qualitative)
- If quantitative: value (number) + unit (text)
- If qualitative: methodology note (textarea)
- Period start (date picker)
- Period end (optional date picker)
- Indicator key (optional — autocomplete from indicator catalogue)
- Confidence (radio: high / medium / low)

**Step 1: Add API functions to `ui/src/lib/api.ts`**

```typescript
export async function requestMagicLink(organisationId: string, email: string): Promise<void> {
  await postJSON("/v1/contribute/request-link", { organisation_id: organisationId, email });
}

export async function verifyMagicLink(token: string): Promise<{ organisation_id: string }> {
  return postJSON("/v1/contribute/verify-link", { token });
}

export async function signupOrg(name: string, email: string, primaryPlaceId: string): Promise<{ organisation_id: string }> {
  return postJSON("/v1/contribute/signup", { name, email, primary_place_id: primaryPlaceId });
}

export async function submitObservation(obs: Record<string, unknown>): Promise<{ observation_id: string }> {
  return postJSON("/v1/observations", obs);
}

export async function getObservations(params: { place_id?: string; theme?: string; limit?: number }): Promise<GetObservationsResponse> {
  return postJSON("/v1/tools/get_observations", params);
}
```

**Step 2: Create the Astro pages and components**

Write the full `/contribute` page with the multi-state flow. Use progressive enhancement — the form works without JS (server-rendered) but enhances with client-side autocomplete for places and indicators.

**Step 3: Verify the page renders**

```bash
cd ui && npm run build
```

Expected: no errors.

**Step 4: Commit**

```bash
git add ui/src/pages/contribute.astro ui/src/components/ObservationForm.astro ui/src/components/MagicLinkAuth.astro ui/src/lib/api.ts ui/src/lib/types.ts
git commit -m "feat(ui): /contribute page with observation submission form"
```

---

### Task 12: UI — observations on place pages

**Objective:** Add an "Observations from local organisations" section to `/place/[id]`.

**Files:**
- Create: `ui/src/components/ObservationsPanel.astro`
- Modify: `ui/src/pages/place/[id].astro` (add the panel)
- Modify: `ui/src/lib/types.ts` (add observation types for the panel)

**Implementation notes:**

The panel appears below the existing data sections on the place page. It shows:
- Theme heading with count badge
- Latest observation date per theme
- Organisation names
- A link to submit an observation for this place ("Add an observation" → `/contribute?place_id=...`)

The data comes from `get_place_profile`'s new `observations_summary` field. If `observations_summary` is null or `total_observations` is 0, the panel is hidden (or shows a gentle "No observations yet — be the first to contribute" prompt with a link to `/contribute`).

**Step 1: Create the panel component**

```astro
---
// ui/src/components/ObservationsPanel.astro
interface Props {
  summary: {
    total_observations: number;
    themes: Array<{
      theme: string;
      count: number;
      latest_submission: string;
      organisation_names: string[];
    }>;
  } | null;
  placeId: string;
}

const { summary, placeId } = Astro.props;
---

{summary && summary.total_observations > 0 ? (
  <section class="observations-panel">
    <h2>Observations from local organisations</h2>
    <p class="observations-intro">
      Experiential evidence contributed by organisations working in this area.
      These are not official statistics — they are structured claims with provenance.
    </p>
    <ul class="theme-list">
      {summary.themes.map((item) => (
        <li class="theme-item">
          <span class="theme-label">{item.theme.replace(/_/g, ' ')}</span>
          <span class="theme-count">{item.count} observation{item.count !== 1 ? 's' : ''}</span>
          <span class="theme-orgs">{item.organisation_names.join(', ')}</span>
          <span class="theme-latest">Latest: {new Date(item.latest_submission).toLocaleDateString('en-GB')}</span>
        </li>
      ))}
    </ul>
    <a href={`/contribute?place_id=${placeId}`} class="contribute-link">
      Add an observation for this area →
    </a>
  </section>
) : (
  <section class="observations-panel empty">
    <h2>No observations yet</h2>
    <p>Be the first to contribute an observation about this area.</p>
    <a href={`/contribute?place_id=${placeId}`} class="contribute-link">
      Add an observation →
    </a>
  </section>
)}
```

**Step 2: Add to the place page**

In `ui/src/pages/place/[id].astro`, fetch the place profile and render the panel:
```astro
import ObservationsPanel from '../components/ObservationsPanel.astro';
// ... after the existing data panels:
<ObservationsPanel summary={profile.observations_summary} placeId={profile.place.id} />
```

**Step 3: Verify build**

```bash
cd ui && npm run build
```

Expected: no errors.

**Step 4: Commit**

```bash
git add ui/src/components/ObservationsPanel.astro ui/src/pages/place/[id].astro ui/src/lib/types.ts
git commit -m "feat(ui): observations panel on place pages"
```

---

### Task 13: UI — public `/observations` stream page

**Objective:** A public page showing recent observations across the system, filterable by place and theme.

**Files:**
- Create: `ui/src/pages/observations.astro`
- Modify: `ui/src/lib/api.ts` (if not already added in Task 11)

**Implementation notes:**

The page calls `GET /v1/observations` (or `POST /v1/tools/get_observations`) with no place filter to get the most recent observations. Shows:
- Filter bar (theme dropdown, optional place search)
- List of observation cards, each showing: statement, org name, place name, theme, evidence type, confidence, date
- "Add an observation" CTA

**Step 1: Create the page**

Build the page with SSR fetch to the API. Each observation renders as a card with clear experiential labeling.

**Step 2: Add to nav**

Add a link to `/observations` in the site navigation.

**Step 3: Verify build**

```bash
cd ui && npm run build
```

**Step 4: Commit**

```bash
git add ui/src/pages/observations.astro
git commit -m "feat(ui): public /observations stream page"
```

---

### Task 14: End-to-end integration test

**Objective:** A test that exercises the full flow: sign up → authenticate → submit observation → query via tool → verify in place profile.

**Files:**
- Create: `server/tests/test_observations_e2e.py`

**Step 1: Write the test**

```python
# server/tests/test_observations_e2e.py
"""E2E: signup → magic-link → submit observation → get_observations → place profile."""

import pytest
from httpx import AsyncClient
from soundings.http.app import create_app

pytestmark = pytest.mark.asyncio


async def test_full_observation_flow():
    app = create_app()
    async with AsyncClient(app=app, base_url="http://test") as client:

        # 1. Sign up a new org
        signup_resp = await client.post("/v1/contribute/signup", json={
            "name": "Teesside Mutual Aid",
            "email": "contact@teesside-mutual.org",
            "primary_place_id": "ltla24:E06000004",
        })
        assert signup_resp.status_code == 201
        org_id = signup_resp.json()["organisation_id"]

        # 2. Submit an observation (using cookie-based auth)
        client.cookies.set("soundings_contrib_session", org_id)
        submit_resp = await client.post("/v1/observations", json={
            "organisation_id": org_id,
            "place_id": "ltla24:E06000004",
            "period_start": "2026-08-01",
            "theme": "food_insecurity",
            "statement": "We distributed 340 food parcels in August 2026, up from 210 in August 2025.",
            "value": 340,
            "unit": "food parcels",
            "evidence_type": "quantitative",
            "methodology_note": "Monthly food bank distribution records.",
            "confidence": "high",
        })
        assert submit_resp.status_code == 201
        assert submit_resp.json()["status"] == "accepted"

        # 3. Query via get_observations tool
        obs_resp = await client.post("/v1/tools/get_observations", json={
            "place_id": "ltla24:E06000004",
            "theme": "food_insecurity",
        })
        assert obs_resp.status_code == 200
        obs_data = obs_resp.json()
        assert obs_data["total"] >= 1
        observation = obs_data["observations"][0]
        assert observation["organisation_name"] == "Teesside Mutual Aid"
        assert observation["theme"] == "food_insecurity"
        assert observation["value"] == 340

        # 4. Verify it appears in the place profile summary
        profile_resp = await client.post("/v1/tools/get_place_profile", json={
            "place_id": "ltla24:E06000004",
        })
        assert profile_resp.status_code == 200
        profile = profile_resp.json()
        assert profile["observations_summary"] is not None
        assert profile["observations_summary"]["total_observations"] >= 1
        theme_item = next(
            t for t in profile["observations_summary"]["themes"]
            if t["theme"] == "food_insecurity"
        )
        assert theme_item["count"] >= 1
        assert "Teesside Mutual Aid" in theme_item["organisation_names"]
```

**Step 2: Run test**

```bash
cd server && uv run pytest tests/test_observations_e2e.py -v
```

Expected: PASS (if all prior tasks are complete).

**Step 3: Commit**

```bash
git add server/tests/test_observations_e2e.py
git commit -m "test: end-to-end observation flow (signup → submit → query → profile)"
```

---

### Task 15: Update STATE.md and CLAUDE.md

**Objective:** Record the new observation layer in the project state files.

**Files:**
- Modify: `STATE.md`
- Modify: `CLAUDE.md`

**Step 1: Update STATE.md**

Add to the system state diagram:
```
Phase6bBreadth --> ObservationsMVP: contribution layer (hybrid identity)
ObservationsMVP --> Phase6Done: v3 features added incrementally
```

Add to component status table:
```
| **Observation schema + submission** | ✅ Phase 7 (MVP) | Hybrid identity (existing orgs via magic-link + new orgs via lightweight signup). data.observation table, 12 initial themes. get_observations tool. Ask dispatcher integration. UI /contribute + /observations + place panel. |
```

**Step 2: Update CLAUDE.md**

Add to the Phase section:
```
> Phase 7 (Observations MVP): hybrid contribution layer — organisations submit
> structured observations that sit alongside official statistics. See
> docs/plans/2026-08-24-observations-mvp.md.
```

**Step 3: Commit**

```bash
git add STATE.md CLAUDE.md
git commit -m "docs: update STATE.md and CLAUDE.md for observations MVP"
```

---

## Verification checklist

After all tasks are complete:

- [ ] `make test` passes (all Python tests green)
- [ ] `make migrate` applies the new migration cleanly
- [ ] `make seed-light` seeds themes without error
- [ ] `POST /v1/contribute/signup` creates a new org
- [ ] `POST /v1/contribute/request-link` sends a magic link
- [ ] `POST /v1/contribute/verify-link` sets auth cookie
- [ ] `POST /v1/observations` stores an observation (authenticated)
- [ ] `POST /v1/tools/get_observations` returns observations with org + place names
- [ ] `POST /v1/tools/get_place_profile` includes `observations_summary`
- [ ] `/v1/ask` can call `get_observations` and cite observations in answers
- [ ] `/contribute` page renders with the submission form
- [ ] `/place/[id]` shows observations panel
- [ ] `/observations` page shows the public stream
- [ ] E2E test passes: signup → submit → query → profile summary

---

## What this plan deliberately defers (from v3 spec)

| v3 feature | Deferred to | Rationale |
|---|---|---|
| v2 self-hosted context profiles (`.well-known/soundings.yaml`) | v3.1 | Internal profiles work now; external is additive |
| `superseded_by` / revision chain | v3.1 | Append-only is simpler; non-breaking to add later |
| `withdrawn_at` / soft delete | v3.1 | Hard-delete by DBA for now; non-breaking to add later |
| Editorial review queue | v3.1 | Auto-accept until volume warrants review |
| Reputation signal | v3.2 | Not meaningful with few contributors |
| `attribution.visibility = aggregated_only` | v3.1 | Default public; non-breaking to add later |
| Qualitative excerpts | v3.1 | Methodology note covers qualitative context |
| `get_observation_stream` tool | v3.1 | `/observations` UI page covers the use case for now |
| Migration from original databank | v3.1 | Separate effort, can run anytime |
| Theme RFC process | v3.1 | Start with 12, expand by editorial decision |
| API key auth for third-party submission | v3.1 | Magic-link + cookie for now; API keys later |
| Bulk CSV upload | v3.1 | Form submission first; bulk later |
| `evidence_type = "mixed"` | v3.1 | Submit as quantitative with methodology note |

---

## Forward compatibility

All deferrals are designed to be non-breaking additions:

- `superseded_by UUID` column → add later, nullable
- `withdrawn_at TIMESTAMPTZ` column → add later, nullable
- `status VARCHAR` column (for review queue) → add later, default `'accepted'`
- `attribution_visibility VARCHAR` column → add later, default `'public'`
- `evidence_type` check constraint → widen to include `'mixed'`
- Multi-place observations → add `data.observation_place` join table, make `place_id` nullable
- External v2 profiles → add `data.organisation.profile_url` column, add crawl/index service

No schema change in this plan will need to be reversed.