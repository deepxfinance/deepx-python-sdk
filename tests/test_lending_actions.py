import os

import deepx_sdk as dx
from _test_output import make_print

print = make_print()  # type: ignore[assignment]

# Transaction test for lending actions via client.lending.*
# Set LENDING_ACTION to choose the call.
# Example:
#   # Optional overrides (point at the internal deployment when developing):
#   # export SUBSTRATE_WS=wss://rpc-testnet.deepx.fi
#   # export EVM_RPC_URL=https://rpc-testnet.deepx.fi
#   export PRIVATE_KEY=0x...
#   export LENDING_PRECOMPILE=0x0000000000000000000000000000000000000450
#   export LENDING_ACTION=deposit
#   export LENDING_SUBACCOUNT=0xYOUR_SUBACCOUNT
#   export LENDING_ASSET=usdc
#   export LENDING_AMOUNT=1000000000000000000
#   python tests/test_lending_actions.py


def require_env(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if not val:
        raise RuntimeError(f"{key} is required")
    return val


def optional_int(key: str) -> int | None:
    raw = os.environ.get(key, "").strip()
    return int(raw) if raw else None


def parse_u8(val: str) -> int:
    n = int(val)
    if n < 0 or n > 0xFF:
        raise ValueError(f"invalid u8: {val}")
    return n


def parse_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes"}


def pick_account(*keys: str, required: bool = False) -> str:
    for key in keys:
        val = os.environ.get(key, "").strip()
        if val:
            return val
    if required:
        raise RuntimeError(f"{keys[0]} is required")
    return ""


def print_tx(res: object) -> None:
    print("result:", res)
    tx_hash = getattr(res, "tx_hash", None)
    event = getattr(res, "event", None)
    if tx_hash:
        print("tx_hash:", tx_hash)
    if event is not None:
        print("event:", event)


def main() -> None:
    substrate_ws = os.environ.get("SUBSTRATE_WS", "").strip()
    evm_rpc_url = os.environ.get("EVM_RPC_URL", "").strip()
    private_key = require_env("PRIVATE_KEY")

    precompile = os.environ.get(
        "LENDING_PRECOMPILE", "0x0000000000000000000000000000000000000450"
    ).strip()

    action = require_env("LENDING_ACTION").lower()
    print("action:", action)

    chain_id = optional_int("CHAIN_ID")
    gas_limit = optional_int("GAS_LIMIT")
    max_fee_per_gas = optional_int("MAX_FEE_PER_GAS")
    max_priority_fee_per_gas = optional_int("MAX_PRIORITY_FEE_PER_GAS")
    use_legacy = parse_bool("USE_LEGACY", False)
    nonce_ms = optional_int("NONCE_MS")
    wait_for_finalized = parse_bool("WAIT_FOR_FINALIZED", True)
    timeout_ms = optional_int("WAIT_TIMEOUT_MS")
    if timeout_ms is not None and timeout_ms <= 0:
        timeout_ms = None

    default_subaccount = pick_account(
        "LENDING_SUBACCOUNT",
        "SUBACCOUNT",
        "VIEW_SUBACCOUNT",
        "ORDER_SUBACCOUNT",
    )
    if not default_subaccount:
        default_subaccount = "0x" + "00" * 20

    client = dx.ChainClient(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        lending_precompile_address=precompile,
        subaccount=default_subaccount,
        chain_id=chain_id,
        gas_limit=gas_limit,
        max_fee_per_gas=max_fee_per_gas,
        max_priority_fee_per_gas=max_priority_fee_per_gas,
        use_legacy=use_legacy,
        nonce_ms=nonce_ms,
        wait_for_finalized=wait_for_finalized,
    )

    if action == "deposit":
        subaccount = pick_account("LENDING_SUBACCOUNT", "SUBACCOUNT", required=True)
        asset = require_env("LENDING_ASSET")
        amount = int(require_env("LENDING_AMOUNT"))
        res = client.lending.deposit(
            subaccount=subaccount,
            asset=asset,
            amount=amount,
            timeout_ms=timeout_ms,
        )
        print_tx(res)
        return

    if action == "deposit_from_subaccount":
        from_subaccount = pick_account("LENDING_FROM_SUBACCOUNT", required=True)
        subaccount = pick_account("LENDING_SUBACCOUNT", "SUBACCOUNT", required=True)
        asset = require_env("LENDING_ASSET")
        amount = int(require_env("LENDING_AMOUNT"))
        auto_borrow = parse_bool("LENDING_AUTO_BORROW", False)
        res = client.lending.deposit_from_subaccount(
            from_subaccount=from_subaccount,
            subaccount=subaccount,
            asset=asset,
            amount=amount,
            auto_borrow=auto_borrow,
            timeout_ms=timeout_ms,
        )
        print_tx(res)
        return

    if action == "withdraw":
        subaccount = pick_account("LENDING_SUBACCOUNT", "SUBACCOUNT", required=True)
        asset = require_env("LENDING_ASSET")
        amount = int(require_env("LENDING_AMOUNT"))
        res = client.lending.withdraw(
            subaccount=subaccount,
            asset=asset,
            amount=amount,
            timeout_ms=timeout_ms,
        )
        print_tx(res)
        return

    if action == "borrow":
        borrower = pick_account("LENDING_BORROWER", "LENDING_SUBACCOUNT", required=True)
        market_id = parse_u8(require_env("LENDING_MARKET_ID"))
        asset = require_env("LENDING_ASSET")
        amount = int(require_env("LENDING_AMOUNT"))
        res = client.lending.borrow(
            borrower=borrower,
            market_id=market_id,
            asset=asset,
            amount=amount,
            timeout_ms=timeout_ms,
        )
        print_tx(res)
        return

    if action == "repay":
        who = pick_account("LENDING_WHO", "LENDING_SUBACCOUNT", required=True)
        market_id = parse_u8(require_env("LENDING_MARKET_ID"))
        asset = require_env("LENDING_ASSET")
        amount = int(require_env("LENDING_AMOUNT"))
        res = client.lending.repay(
            who=who,
            market_id=market_id,
            asset=asset,
            amount=amount,
            timeout_ms=timeout_ms,
        )
        print_tx(res)
        return

    if action == "buy_quota":
        account = pick_account("LENDING_ACCOUNT", "LENDING_SUBACCOUNT", required=True)
        quota = int(require_env("LENDING_QUOTA"))
        res = client.lending.buy_quota(
            account=account,
            quota=quota,
            timeout_ms=timeout_ms,
        )
        print_tx(res)
        return

    if action == "bridge_invoke":
        uid = require_env("LENDING_UID")
        amount = int(require_env("LENDING_AMOUNT"))
        custom_data = require_env("LENDING_CUSTOM_DATA")
        res = client.lending.bridge_invoke(
            uid=uid,
            amount=amount,
            custom_data=custom_data,
            timeout_ms=timeout_ms,
        )
        print_tx(res)
        return

    if action == "withdraw_and_swap":
        subaccount = pick_account("LENDING_SUBACCOUNT", "SUBACCOUNT", required=True)
        asset = require_env("LENDING_ASSET")
        amount = int(require_env("LENDING_AMOUNT"))
        dst_chain_id = int(require_env("LENDING_DST_CHAIN_ID"))
        token_id = int(require_env("LENDING_TOKEN_ID"))
        dst_recipient = require_env("LENDING_DST_RECIPIENT")
        refund_address = require_env("LENDING_REFUND_ADDRESS")
        salt = require_env("LENDING_SALT")
        custom_data = require_env("LENDING_CUSTOM_DATA")
        signature = require_env("LENDING_SIGNATURE")
        consumer_address = require_env("LENDING_CONSUMER_ADDRESS")
        res = client.lending.withdraw_and_swap(
            subaccount=subaccount,
            asset=asset,
            amount=amount,
            dst_chain_id=dst_chain_id,
            token_id=token_id,
            dst_recipient=dst_recipient,
            refund_address=refund_address,
            salt=salt,
            custom_data=custom_data,
            signature=signature,
            consumer_address=consumer_address,
            timeout_ms=timeout_ms,
        )
        print_tx(res)
        return

    if action == "borrow_and_swap":
        borrower = pick_account("LENDING_BORROWER", "LENDING_SUBACCOUNT", required=True)
        market_id = parse_u8(require_env("LENDING_MARKET_ID"))
        asset = require_env("LENDING_ASSET")
        amount = int(require_env("LENDING_AMOUNT"))
        dst_chain_id = int(require_env("LENDING_DST_CHAIN_ID"))
        token_id = int(require_env("LENDING_TOKEN_ID"))
        dst_recipient = require_env("LENDING_DST_RECIPIENT")
        refund_address = require_env("LENDING_REFUND_ADDRESS")
        salt = require_env("LENDING_SALT")
        custom_data = require_env("LENDING_CUSTOM_DATA")
        signature = require_env("LENDING_SIGNATURE")
        consumer_address = require_env("LENDING_CONSUMER_ADDRESS")
        res = client.lending.borrow_and_swap(
            borrower=borrower,
            market_id=market_id,
            asset=asset,
            amount=amount,
            dst_chain_id=dst_chain_id,
            token_id=token_id,
            dst_recipient=dst_recipient,
            refund_address=refund_address,
            salt=salt,
            custom_data=custom_data,
            signature=signature,
            consumer_address=consumer_address,
            timeout_ms=timeout_ms,
        )
        print_tx(res)
        return

    raise RuntimeError(f"unknown LENDING_ACTION: {action}")


if __name__ == "__main__":
    main()
