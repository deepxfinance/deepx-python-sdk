from __future__ import annotations

from dataclasses import dataclass

from .api import ApiClient
from .client import ChainClient
from .bridge import BridgeApi, BridgeServiceClient


@dataclass
class SDK:
    chain: ChainClient
    api: ApiClient
    bridge: BridgeApi | None = None
    bridge_service: BridgeServiceClient | None = None
