from __future__ import annotations

import pytest

import deepx_sdk as dx
from deepx_sdk._market_resolver import MarketResolver


class _MarketsEndpoint:
    def __init__(self, response) -> None:
        self.response = response
        self.calls = 0

    def markets(self):
        self.calls += 1
        return self.response


class _FakeApiClient:
    def __init__(self, *, perp, spot, lending) -> None:
        v1 = type("_FakeV1", (), {})()
        v1.perp = _MarketsEndpoint(perp)
        v1.spot = _MarketsEndpoint(spot)
        v1.lending = _MarketsEndpoint(lending)
        self.v1 = v1


class _FlakyMarketsEndpoint:
    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.calls = 0

    def markets(self):
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_market_resolver_resolves_symbols_and_caches_market_lists() -> None:
    api = _FakeApiClient(
        perp={"data": {"items": [{"symbol": "ETH-USDC", "marketId": "3"}]}},
        spot={"items": [{"market_symbol": "ETH-USDC", "market_id": "0xabc"}]},
        lending={"data": [{"asset": "USDC"}]},
    )
    resolver = MarketResolver(net="testnet", api_client=api)

    assert resolver.resolve_perp_market_id("eth-usdc") == 3
    assert resolver.resolve_perp_market_id("ETH-USDC") == 3
    assert resolver.resolve_spot_pair("eth-usdc") == "0xabc"
    assert resolver.resolve_spot_pair("ETH-USDC") == "0xabc"
    assert resolver.resolve_lending_asset("usdc") == "USDC"
    assert resolver.resolve_lending_asset("USDC") == "USDC"

    assert api.v1.perp.calls == 1
    assert api.v1.spot.calls == 1
    assert api.v1.lending.calls == 1


def test_market_resolver_preload_and_refresh() -> None:
    api = _FakeApiClient(
        perp=[{"symbol": "ETH-USDC", "marketId": "3"}],
        spot=[{"symbol": "ETH-USDC", "marketId": "0xabc"}],
        lending=[{"asset": "USDC"}],
    )
    resolver = MarketResolver(net="testnet", api_client=api)

    resolver.preload()
    resolver.preload()

    assert api.v1.perp.calls == 1
    assert api.v1.spot.calls == 1
    assert api.v1.lending.calls == 1

    resolver.refresh()

    assert api.v1.perp.calls == 2
    assert api.v1.spot.calls == 2
    assert api.v1.lending.calls == 2
    assert resolver.resolve_perp_market_id("ETH-USDC") == 3
    assert resolver.resolve_spot_pair("ETH-USDC") == "0xabc"
    assert resolver.resolve_lending_asset("USDC") == "USDC"


def test_market_resolver_failed_load_does_not_poison_cache() -> None:
    api = type("_Api", (), {})()
    v1 = type("_V1", (), {})()
    v1.perp = _FlakyMarketsEndpoint(
        RuntimeError("temporary REST failure"),
        [{"symbol": "ETH-USDC", "marketId": 3}],
    )
    v1.spot = _MarketsEndpoint([])
    v1.lending = _MarketsEndpoint([])
    api.v1 = v1
    resolver = MarketResolver(net="testnet", api_client=api)

    with pytest.raises(RuntimeError, match="temporary REST failure"):
        resolver.resolve_perp_market_id("ETH-USDC")

    assert resolver._perp_market_ids is None
    assert resolver.resolve_perp_market_id("ETH-USDC") == 3
    assert v1.perp.calls == 2


def test_market_resolver_unknown_perp_or_spot_symbol_raises() -> None:
    resolver = MarketResolver(
        net="testnet",
        api_client=_FakeApiClient(perp=[], spot=[], lending=[]),
    )

    with pytest.raises(dx.MarketNotFoundError, match="unknown perp symbol"):
        resolver.resolve_perp_market_id("DOGE-USDC")

    with pytest.raises(dx.MarketNotFoundError, match="unknown spot symbol"):
        resolver.resolve_spot_pair("DOGE-USDC")

    with pytest.raises(ValueError):
        resolver.resolve_perp_market_id("DOGE-USDC")


def test_market_resolver_lending_bytes_are_passthrough_without_rest_lookup() -> None:
    api = _FakeApiClient(perp=[], spot=[], lending=[])
    resolver = MarketResolver(net="testnet", api_client=api)

    assert resolver.resolve_lending_asset(b"USDC") == b"USDC"
    assert api.v1.lending.calls == 0


def test_market_resolver_unknown_lending_symbol_raises() -> None:
    api = _FakeApiClient(perp=[], spot=[], lending=[{"asset": "USDC"}])
    resolver = MarketResolver(net="testnet", api_client=api)

    assert resolver.resolve_lending_asset("USDC") == "USDC"
    assert api.v1.lending.calls == 1

    with pytest.raises(dx.MarketNotFoundError, match="unknown lending symbol"):
        resolver.resolve_lending_asset("ETH-USDC")

    assert api.v1.lending.calls == 1


def test_market_resolver_skips_malformed_items_and_requires_api_base_url() -> None:
    api = _FakeApiClient(
        perp=[None, {"symbol": "", "marketId": "3"}, {"name": "BTC-USDC", "id": 4}],
        spot=[None, {"symbol": "BTC-USDC", "pairId": "0xpair"}],
        lending=[None, {"symbol": "BTC"}],
    )
    resolver = MarketResolver(net="testnet", api_client=api)

    assert resolver.resolve_perp_market_id("BTC-USDC") == 4
    assert resolver.resolve_spot_pair("BTC-USDC") == "0xpair"
    assert resolver.resolve_lending_asset("BTC") == "BTC"

    with pytest.raises(ValueError, match="api_base_url is required"):
        MarketResolver(net="staging").resolve_perp_market_id("BTC-USDC")
