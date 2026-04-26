"""Tests for :mod:`posrat.ai.config` — singleton DAO + migration v4."""

from __future__ import annotations

import pytest

from posrat.ai.config import (
    DEFAULT_MODEL_ID,
    DEFAULT_REGION,
    AISettings,
    load_ai_settings,
    save_ai_settings,
)
from posrat.system.system_db import (
    CURRENT_SYSTEM_SCHEMA_VERSION,
    open_system_db,
    resolve_system_db_path,
)


@pytest.fixture
def system_db(tmp_path):
    """Open a fresh ``system.sqlite`` under a tmp data dir."""

    db = open_system_db(resolve_system_db_path(tmp_path))
    try:
        yield db
    finally:
        db.close()


def test_migration_creates_ai_settings_table(system_db):
    # Migration v4 must push the schema to at least version 4 and the
    # ``ai_settings`` table must exist after open_system_db finishes.
    version_row = system_db.execute(
        "SELECT version FROM schema_version"
    ).fetchone()
    assert version_row is not None
    assert version_row[0] >= 4
    assert CURRENT_SYSTEM_SCHEMA_VERSION >= 4

    tables = {
        r[0]
        for r in system_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "ai_settings" in tables


def test_load_returns_defaults_when_row_missing(system_db):
    settings = load_ai_settings(system_db)
    assert settings == AISettings.default()
    assert settings.enabled is False
    assert settings.model_id == DEFAULT_MODEL_ID
    assert settings.region == DEFAULT_REGION
    assert settings.system_prompt is None
    assert settings.mcp_config_json is None


def test_save_then_load_round_trips(system_db):
    saved = save_ai_settings(
        system_db,
        enabled=True,
        model_id="anthropic.claude-3-sonnet-v1",
        region="us-east-1",
        system_prompt="Be helpful.",
        mcp_config_json='{"mcpServers": {}}',
    )
    loaded = load_ai_settings(system_db)
    # The ``updated_at`` timestamp is filled by the DAO so both
    # snapshots must carry the same value.
    assert saved == loaded
    assert loaded.enabled is True
    assert loaded.model_id == "anthropic.claude-3-sonnet-v1"
    assert loaded.region == "us-east-1"
    assert loaded.system_prompt == "Be helpful."
    assert loaded.mcp_config_json == '{"mcpServers": {}}'
    assert loaded.updated_at is not None


def test_save_is_upsert_singleton(system_db):
    save_ai_settings(
        system_db,
        enabled=True,
        model_id="m1",
        region="r1",
    )
    save_ai_settings(
        system_db,
        enabled=False,
        model_id="m2",
        region="r2",
    )
    rows = system_db.execute(
        "SELECT COUNT(*) FROM ai_settings"
    ).fetchone()
    assert rows[0] == 1

    loaded = load_ai_settings(system_db)
    assert loaded.enabled is False
    assert loaded.model_id == "m2"
    assert loaded.region == "r2"


def test_save_normalises_empty_optional_strings_to_none(system_db):
    saved = save_ai_settings(
        system_db,
        enabled=True,
        model_id="m",
        region="r",
        system_prompt="   ",
        mcp_config_json="",
    )
    assert saved.system_prompt is None
    assert saved.mcp_config_json is None


def test_save_rejects_blank_required_fields(system_db):
    with pytest.raises(ValueError):
        save_ai_settings(
            system_db,
            enabled=True,
            model_id=" ",
            region="r",
        )
    with pytest.raises(ValueError):
        save_ai_settings(
            system_db,
            enabled=True,
            model_id="m",
            region="",
        )


def test_save_trims_required_field_whitespace(system_db):
    saved = save_ai_settings(
        system_db,
        enabled=True,
        model_id="  m1  ",
        region="\teu-west-1\n",
    )
    assert saved.model_id == "m1"
    assert saved.region == "eu-west-1"
