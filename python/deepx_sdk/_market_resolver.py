from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ._errors import MarketNotFoundError
from ._network import allowed_nets, network_config


def _normalize_symbol(value: str) -> str:
    return str(value).strip().upper()


def _payload(value: Any) -> Any:
    if isinstance(value, dict) and "data" in value:
        return value["data"]
    return value


def _as_list(value: Any) -> list[Any]:
    value = _payload(value)
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        return value["items"]
    return []


def _first_present(item: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in item and item[name] not in (None, ""):
            return item[name]
    return None


@dataclass
class MarketResolver:
    net: str
    api_base_url: str | None = None
    api_client: Any = None
    _perp_market_ids: dict[str, int] | None = field(default=None, init=False, repr=False)
    _spot_pairs: dict[str, str] | None = field(default=None, init=False, repr=False)
    _lending_assets: dict[str, str] | None = field(default=None, init=False, repr=False)

    def _client(self) -> Any:
        if self.api_client is not None:
            return self.api_client

        from .api import ApiClient

        try:
            base_url = self.api_base_url or network_config(self.net).api_base_url
        except ValueError as exc:
            raise ValueError(
                f"api_base_url is required when net is not one of: {allowed_nets()}"
            ) from exc
        self.api_client = ApiClient(base_url=base_url, net=self.net)
        return self.api_client

    def preload(self) -> None:
        self._load_perp_market_ids()
        self._load_spot_pairs()
        self._load_lending_assets()

    def refresh(self) -> None:
        self._perp_market_ids = None
        self._spot_pairs = None
        self._lending_assets = None
        self.preload()

    def _load_perp_market_ids(self) -> dict[str, int]:
        if self._perp_market_ids is None:
            market_ids: dict[str, int] = {}
            for item in _as_list(self._client().v1.perp.markets()):
                if not isinstance(item, dict):
                    continue
                item_symbol = _first_present(item, ("symbol", "name", "marketSymbol", "market_symbol"))
                market_id = _first_present(item, ("marketId", "market_id", "id"))
                if item_symbol is not None and market_id is not None:
                    market_ids[_normalize_symbol(str(item_symbol))] = int(market_id)
            self._perp_market_ids = market_ids
        return self._perp_market_ids

    def _load_spot_pairs(self) -> dict[str, str]:
        if self._spot_pairs is None:
            pairs: dict[str, str] = {}
            for item in _as_list(self._client().v1.spot.markets()):
                if not isinstance(item, dict):
                    continue
                item_symbol = _first_present(item, ("symbol", "name", "marketSymbol", "market_symbol"))
                pair = _first_present(item, ("pair", "pairId", "pair_id", "marketId", "market_id", "id"))
                if item_symbol is not None and pair is not None:
                    pairs[_normalize_symbol(str(item_symbol))] = str(pair)
            self._spot_pairs = pairs
        return self._spot_pairs

    def _load_lending_assets(self) -> dict[str, str]:
        if self._lending_assets is None:
            assets: dict[str, str] = {}
            for item in _as_list(self._client().v1.lending.markets()):
                if not isinstance(item, dict):
                    continue
                asset = _first_present(item, ("asset", "symbol", "baseAsset", "base_asset"))
                if asset is not None:
                    assets[_normalize_symbol(str(asset))] = str(asset)
            self._lending_assets = assets
        return self._lending_assets

    def resolve_perp_market_id(self, symbol: str) -> int:
        key = _normalize_symbol(symbol)
        market_ids = self._load_perp_market_ids()
        try:
            return market_ids[key]
        except KeyError as exc:
            raise MarketNotFoundError(f"unknown perp symbol: {symbol}") from exc

    def resolve_spot_pair(self, symbol: str) -> str:
        key = _normalize_symbol(symbol)
        pairs = self._load_spot_pairs()
        try:
            return pairs[key]
        except KeyError as exc:
            raise MarketNotFoundError(f"unknown spot symbol: {symbol}") from exc

    def resolve_lending_asset(self, symbol: str | bytes) -> str | bytes:
        if isinstance(symbol, bytes):
            return symbol
        key = _normalize_symbol(symbol)
        assets = self._load_lending_assets()
        try:
            return assets[key]
        except KeyError as exc:
            raise MarketNotFoundError(f"unknown lending symbol: {symbol}") from exc
