"""
IPCSkill – Configuration dataclasses.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Microsoft Intune's own well-known public client ID.
# The Explorer and device-inventory endpoints only accept tokens issued to this client;
# custom app registrations are blocked by Microsoft for those APIs.
INTUNE_CLIENT_ID = "5926fc8e-304e-4f59-8bed-58ca97cc39a4"


@dataclass
class LocalTokenStoreConfig:
    """Settings for local encrypted token storage."""

    store_dir: str = ""
    """Directory where encrypted token files are stored.
    Defaults to ``~/.explorer_skill`` when left empty (shared with ExplorerSkill)."""


@dataclass
class IPCSkillConfig:
    """Top-level configuration for IPCSkill."""

    token_store: LocalTokenStoreConfig = field(default_factory=LocalTokenStoreConfig)
    graph_base_url: str = "https://graph.microsoft.com/beta"
    """Base URL for Microsoft Graph API calls (beta endpoint for Intune)."""
