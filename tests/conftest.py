"""Shared pytest fixtures for IPCSkill tests."""
from __future__ import annotations

import pytest

from explorer_skill import ExplorerSkillConfig
from explorer_skill.config import LocalTokenStoreConfig


@pytest.fixture()
def config(tmp_path) -> ExplorerSkillConfig:
    return ExplorerSkillConfig(
        token_store=LocalTokenStoreConfig(store_dir=str(tmp_path)),
        graph_base_url="https://graph.microsoft.com/beta",
    )
