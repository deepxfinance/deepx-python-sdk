import json
import os
import time
from urllib.request import Request, urlopen

import deepx_sdk as dx
from _test_output import make_print

from deepx_sdk._native import build_signed_tx

print = make_print()  # type: ignore[assignment]

# Transaction test for subaccount actions via client.subaccount_client.*
# Set SUBACCOUNT_ACTION to choose the call.
# Example:
#   export SUBSTRATE_WS=ws://127.0.0.1:9944
#   export EVM_RPC_URL=http://127.0.0.1:8545
#   export PRIVATE_KEY=0x...
#   export SUBACCOUNT_PRECOMPILE=0x0000000000000000000000000000000000000451
#   export SUBACCOUNT_ACTION=initialize
#   export SUBACCOUNT_NAME=test-subaccount
#   python tests/test_subaccount_actions.py


def require_env(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if not val:
        raise RuntimeError(f"{key} is required")
    return val


def parse_symbol_bytes(raw: str) -> bytes:
    raw = raw.strip()
    if raw.startswith("0x"):
        return bytes.fromhex(raw[2:])
    return raw.encode("utf-8")


def get_tx_count(evm_rpc_url: str, address: str) -> int:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_getTransactionCount",
        "params": [address, "pending"],
    }
    req = Request(
        evm_rpc_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urlopen(req) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if "error" in body:
        raise RuntimeError(f"eth_getTransactionCount error: {body['error']}")
    return int(body["result"], 16)


def print_tx(res: object) -> None:
    tx_hash = getattr(res, "tx_hash", None)
    event = getattr(res, "event", None)
    if tx_hash:
        print("tx_hash:", tx_hash)
    if event:
        print("event:", event)


def print_unsigned_tx_hash(
    *,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    data: bytes,
    nonce_ms: int,
    gas_limit: int,
) -> None:
    signed = build_signed_tx(
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        precompile_address=precompile_address,
        data=data,
        nonce_ms=nonce_ms,
        gas_limit=gas_limit,
    )
    print("tx_hash (predicted):", signed.tx_hash)


def main() -> None:
    substrate_ws = os.environ.get("SUBSTRATE_WS", "ws://127.0.0.1:9944").strip()
    evm_rpc_url = require_env("EVM_RPC_URL")
    private_key = require_env("PRIVATE_KEY")

    precompile = os.environ.get(
        "SUBACCOUNT_PRECOMPILE", "0x0000000000000000000000000000000000000451"
    ).strip()

    action = require_env("SUBACCOUNT_ACTION").lower()
    print("action:", action)

    subaccount = (
        os.environ.get("SUBACCOUNT", "").strip()
        or os.environ.get("VIEW_SUBACCOUNT", "").strip()
        or os.environ.get("ORDER_SUBACCOUNT", "").strip()
    )

    if not subaccount:
        subaccount = "0x" + "00" * 20

    signer = build_signed_tx(
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        precompile_address=precompile,
        data=b"",
        gas_limit=21000,
        nonce_ms=0,
        use_timestamp_nonce=False,
    ).signer
    print("signer_address:", signer)

    use_timestamp_nonce = os.environ.get("USE_TIMESTAMP_NONCE", "false").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    nonce_ms_raw = os.environ.get("NONCE_MS", "").strip()
    if nonce_ms_raw:
        nonce_ms = int(nonce_ms_raw)
    elif use_timestamp_nonce:
        nonce_ms = int(time.time() * 1000)
    else:
        nonce_ms = get_tx_count(evm_rpc_url, signer)
    print("use_timestamp_nonce:", use_timestamp_nonce)
    print("nonce_value:", nonce_ms)
    gas_limit = int(os.environ.get("GAS_LIMIT", "500000").strip() or 500000)
    print("gas_limit:", gas_limit)
    wait_for_finalized = os.environ.get("WAIT_FOR_FINALIZED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    print("wait_for_finalized:", wait_for_finalized)
    timeout_ms_raw = os.environ.get("WAIT_TIMEOUT_MS", "").strip()
    timeout_ms = int(timeout_ms_raw) if timeout_ms_raw else None
    if timeout_ms is not None and timeout_ms <= 0:
        timeout_ms = None
    print("timeout_ms:", timeout_ms)

    client = dx.ChainClient(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        subaccount_precompile_address=precompile,
        subaccount=subaccount,
    )

    if action == "initialize":
        name = os.environ.get("SUBACCOUNT_NAME", "test-subaccount")
        from deepx_sdk._abi import encode_call

        data = encode_call("initializeSubaccount(bytes)", ["bytes"], [name.encode("utf-8")])
        print_unsigned_tx_hash(
            evm_rpc_url=evm_rpc_url,
            private_key=private_key,
            precompile_address=precompile,
            data=data,
            nonce_ms=nonce_ms,
            gas_limit=gas_limit,
        )
        res = client.subaccount_client.initialize_subaccount(
            name=name,
            nonce=nonce_ms,
            gas_limit=gas_limit,
            wait_for_finalized=wait_for_finalized,
            timeout_ms=timeout_ms,
        )
        print("initialize_subaccount:", res)
        print_tx(res)
        return

    if action == "delete":
        target = require_env("SUBACCOUNT_TARGET")
        from deepx_sdk._abi import encode_call, normalize_address
        data = encode_call("deleteSubaccount(address)", ["address"], [normalize_address(target)])
        print_unsigned_tx_hash(
            evm_rpc_url=evm_rpc_url,
            private_key=private_key,
            precompile_address=precompile,
            data=data,
            nonce_ms=nonce_ms,
            gas_limit=gas_limit,
        )
        res = client.subaccount_client.delete_subaccount(
            subaccount=target,
            nonce=nonce_ms,
            gas_limit=gas_limit,
            wait_for_finalized=wait_for_finalized,
            timeout_ms=timeout_ms,
        )
        print("delete_subaccount:", res)
        print_tx(res)
        return

    if action == "rename":
        target = require_env("SUBACCOUNT_TARGET")
        new_name = require_env("SUBACCOUNT_NEW_NAME")
        from deepx_sdk._abi import encode_call, normalize_address
        data = encode_call(
            "renameSubaccount(address,bytes)",
            ["address", "bytes"],
            [normalize_address(target), new_name.encode("utf-8")],
        )
        print_unsigned_tx_hash(
            evm_rpc_url=evm_rpc_url,
            private_key=private_key,
            precompile_address=precompile,
            data=data,
            nonce_ms=nonce_ms,
            gas_limit=gas_limit,
        )
        res = client.subaccount_client.rename_subaccount(
            subaccount=target,
            new_name=new_name,
            nonce=nonce_ms,
            gas_limit=gas_limit,
            wait_for_finalized=wait_for_finalized,
            timeout_ms=timeout_ms,
        )
        print("rename_subaccount:", res)
        print_tx(res)
        return

    if action == "set_delegate":
        target = require_env("SUBACCOUNT_TARGET")
        delegate = require_env("DELEGATE_ACCOUNT")
        from deepx_sdk._abi import encode_call, normalize_address
        data = encode_call(
            "setDelegateAccount(address,address)",
            ["address", "address"],
            [normalize_address(target), normalize_address(delegate)],
        )
        print_unsigned_tx_hash(
            evm_rpc_url=evm_rpc_url,
            private_key=private_key,
            precompile_address=precompile,
            data=data,
            nonce_ms=nonce_ms,
            gas_limit=gas_limit,
        )
        res = client.subaccount_client.set_delegate_account(
            subaccount=target,
            delegate=delegate,
            nonce=nonce_ms,
            gas_limit=gas_limit,
            wait_for_finalized=wait_for_finalized,
            timeout_ms=timeout_ms,
        )
        print("set_delegate_account:", res)
        print_tx(res)
        return

    if action == "set_spot_margin":
        target = require_env("SUBACCOUNT_TARGET")
        enable = os.environ.get("SPOT_MARGIN_ENABLE", "true").strip().lower() in {"1", "true"}
        from deepx_sdk._abi import encode_call, normalize_address
        data = encode_call(
            "setSpotMargin(address,bool)",
            ["address", "bool"],
            [normalize_address(target), enable],
        )
        print_unsigned_tx_hash(
            evm_rpc_url=evm_rpc_url,
            private_key=private_key,
            precompile_address=precompile,
            data=data,
            nonce_ms=nonce_ms,
            gas_limit=gas_limit,
        )
        res = client.subaccount_client.set_spot_margin(
            subaccount=target,
            enable_spot_margin=enable,
            nonce=nonce_ms,
            gas_limit=gas_limit,
            wait_for_finalized=wait_for_finalized,
            timeout_ms=timeout_ms,
        )
        print("set_spot_margin:", res)
        print_tx(res)
        return

    if action == "liquidate_perp_transfer":
        from deepx_sdk._abi import encode_call, normalize_address

        market_index = int(os.environ.get("LIQ_MARKET_INDEX", "3").strip() or 3)
        liquidator_max_base_amount = int(require_env("LIQ_MAX_BASE_AMOUNT"))
        target_subaccount = require_env("LIQ_TARGET_SUBACCOUNT")
        liquidator = require_env("LIQ_LIQUIDATOR")
        limit_price_raw = os.environ.get("LIQ_LIMIT_PRICE", "").strip()
        limit_price = int(limit_price_raw) if limit_price_raw else None

        data = encode_call(
            "liquidatePerpByTransfer(uint16,uint128,uint128,address,address)",
            ["uint16", "uint128", "uint128", "address", "address"],
            [
                market_index,
                liquidator_max_base_amount,
                0 if limit_price is None else limit_price,
                normalize_address(target_subaccount),
                normalize_address(liquidator),
            ],
        )
        print_unsigned_tx_hash(
            evm_rpc_url=evm_rpc_url,
            private_key=private_key,
            precompile_address=precompile,
            data=data,
            nonce_ms=nonce_ms,
            gas_limit=gas_limit,
        )
        res = client.subaccount_client.liquidate_perp_by_transfer(
            market_index=market_index,
            liquidator_max_base_amount=liquidator_max_base_amount,
            target_subaccount=target_subaccount,
            liquidator=liquidator,
            limit_price=limit_price,
            nonce=nonce_ms,
            gas_limit=gas_limit,
            wait_for_finalized=wait_for_finalized,
            timeout_ms=timeout_ms,
        )
        print("liquidate_perp_by_transfer:", res)
        print_tx(res)
        return

    if action == "liquidate_spot_transfer":
        from deepx_sdk._abi import encode_call, normalize_address

        asset_symbol = os.environ.get("LIQ_ASSET_SYMBOL", "eth")
        liability_symbol = os.environ.get("LIQ_LIABILITY_SYMBOL", "usdc")
        target_account_addr = require_env("LIQ_TARGET_SUBACCOUNT")
        liquidator = require_env("LIQ_LIQUIDATOR")
        limit_price_raw = os.environ.get("LIQ_LIMIT_PRICE", "").strip()
        limit_price = int(limit_price_raw) if limit_price_raw else None
        liquidator_max_liability_transfer = int(require_env("LIQ_MAX_LIABILITY_TRANSFER"))
        lending_market_id = int(os.environ.get("LIQ_LENDING_MARKET_ID", "1").strip() or 1)

        data = encode_call(
            "liquidateSpotByTransfer(bytes,bytes,address,address,uint128,uint128,uint8)",
            ["bytes", "bytes", "address", "address", "uint128", "uint128", "uint8"],
            [
                parse_symbol_bytes(asset_symbol),
                parse_symbol_bytes(liability_symbol),
                normalize_address(target_account_addr),
                normalize_address(liquidator),
                0 if limit_price is None else limit_price,
                liquidator_max_liability_transfer,
                lending_market_id,
            ],
        )
        print_unsigned_tx_hash(
            evm_rpc_url=evm_rpc_url,
            private_key=private_key,
            precompile_address=precompile,
            data=data,
            nonce_ms=nonce_ms,
            gas_limit=gas_limit,
        )
        res = client.subaccount_client.liquidate_spot_by_transfer(
            asset_symbol=asset_symbol,
            liability_symbol=liability_symbol,
            target_account_addr=target_account_addr,
            liquidator=liquidator,
            limit_price=limit_price,
            liquidator_max_liability_transfer=liquidator_max_liability_transfer,
            lending_market_id=lending_market_id,
            nonce=nonce_ms,
            gas_limit=gas_limit,
            wait_for_finalized=wait_for_finalized,
            timeout_ms=timeout_ms,
        )
        print("liquidate_spot_by_transfer:", res)
        print_tx(res)
        return

    if action == "liquidate_by_market":
        from deepx_sdk._abi import encode_call, normalize_address

        target_subaccount = require_env("LIQ_TARGET_SUBACCOUNT")
        liquidator = require_env("LIQ_LIQUIDATOR")
        data = encode_call(
            "liquidateByMarket(address,address)",
            ["address", "address"],
            [normalize_address(target_subaccount), normalize_address(liquidator)],
        )
        print_unsigned_tx_hash(
            evm_rpc_url=evm_rpc_url,
            private_key=private_key,
            precompile_address=precompile,
            data=data,
            nonce_ms=nonce_ms,
            gas_limit=gas_limit,
        )
        res = client.subaccount_client.liquidate_by_market(
            target_subaccount=target_subaccount,
            liquidator=liquidator,
            nonce=nonce_ms,
            gas_limit=gas_limit,
            wait_for_finalized=wait_for_finalized,
            timeout_ms=timeout_ms,
        )
        print("liquidate_by_market:", res)
        print_tx(res)
        return

    if action == "create_oct":
        account = require_env("OCT_ACCOUNT")
        print("oct_account:", account)
        quota_raw = os.environ.get("OCT_QUOTA", "").strip()
        if quota_raw:
            print("OCT_QUOTA is ignored by current precompile:", quota_raw)
        from deepx_sdk._abi import encode_call, normalize_address
        data = encode_call(
            "createOneClickTradingAccount(address)",
            ["address"],
            [normalize_address(account)],
        )
        print_unsigned_tx_hash(
            evm_rpc_url=evm_rpc_url,
            private_key=private_key,
            precompile_address=precompile,
            data=data,
            nonce_ms=nonce_ms,
            gas_limit=gas_limit,
        )
        res = client.subaccount_client.create_one_click_trading_account(
            new_account=account,
            nonce=nonce_ms,
            gas_limit=gas_limit,
            wait_for_finalized=wait_for_finalized,
            timeout_ms=timeout_ms,
        )
        print("create_one_click_trading_account:", res)
        print_tx(res)
        return

    if action == "delete_oct":
        account = require_env("OCT_ACCOUNT")
        from deepx_sdk._abi import encode_call, normalize_address
        data = encode_call(
            "deleteOneClickTradingAccount(address)",
            ["address"],
            [normalize_address(account)],
        )
        print_unsigned_tx_hash(
            evm_rpc_url=evm_rpc_url,
            private_key=private_key,
            precompile_address=precompile,
            data=data,
            nonce_ms=nonce_ms,
            gas_limit=gas_limit,
        )
        res = client.subaccount_client.delete_one_click_trading_account(
            account=account,
            nonce=nonce_ms,
            gas_limit=gas_limit,
            wait_for_finalized=wait_for_finalized,
            timeout_ms=timeout_ms,
        )
        print("delete_one_click_trading_account:", res)
        print_tx(res)
        return

    if action == "enable_oct":
        account = require_env("OCT_ACCOUNT")
        from deepx_sdk._abi import encode_call, normalize_address
        data = encode_call(
            "enableOnClickTradingAccount(address)",
            ["address"],
            [normalize_address(account)],
        )
        print_unsigned_tx_hash(
            evm_rpc_url=evm_rpc_url,
            private_key=private_key,
            precompile_address=precompile,
            data=data,
            nonce_ms=nonce_ms,
            gas_limit=gas_limit,
        )
        res = client.subaccount_client.enable_one_click_trading_account(
            account=account,
            nonce=nonce_ms,
            gas_limit=gas_limit,
            wait_for_finalized=wait_for_finalized,
            timeout_ms=timeout_ms,
        )
        print("enable_one_click_trading_account:", res)
        print_tx(res)
        return

    if action == "disable_oct":
        account = require_env("OCT_ACCOUNT")
        from deepx_sdk._abi import encode_call, normalize_address
        data = encode_call(
            "disableOnClickTradingAccount(address)",
            ["address"],
            [normalize_address(account)],
        )
        print_unsigned_tx_hash(
            evm_rpc_url=evm_rpc_url,
            private_key=private_key,
            precompile_address=precompile,
            data=data,
            nonce_ms=nonce_ms,
            gas_limit=gas_limit,
        )
        res = client.subaccount_client.disable_one_click_trading_account(
            account=account,
            nonce=nonce_ms,
            gas_limit=gas_limit,
            wait_for_finalized=wait_for_finalized,
            timeout_ms=timeout_ms,
        )
        print("disable_one_click_trading_account:", res)
        print_tx(res)
        return

    raise RuntimeError(
        "Unknown SUBACCOUNT_ACTION. Use one of: initialize, delete, rename, set_delegate, "
        "set_spot_margin, liquidate_perp_transfer, liquidate_spot_transfer, "
        "liquidate_by_market, create_oct, delete_oct, enable_oct, disable_oct"
    )


if __name__ == "__main__":
    main()
