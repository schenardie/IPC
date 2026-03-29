"""Shared pytest fixtures for IPCSkill tests."""
from __future__ import annotations

import pytest

from ipc_skill import IPCSkillConfig
from ipc_skill.config import LocalTokenStoreConfig


@pytest.fixture()
def config(tmp_path) -> IPCSkillConfig:
    return IPCSkillConfig(
        token_store=LocalTokenStoreConfig(store_dir=str(tmp_path)),
        graph_base_url="https://graph.microsoft.com/beta",
    )
