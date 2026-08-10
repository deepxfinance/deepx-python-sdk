"""EVM -> EVM bridge example: DeepX (chain 4846) -> Ethereum Sepolia.

DeepX's MultiTokenBridge is a Bool Network consumer. The flow:

1. ``getBridgeFee`` quotes the relayer fee in the source chain's gas token.
2. An off-chain **authorizer** service issues an EIP-712 approval signature
   (``sign_api_base`` — the rbool-bridge backend).
3. ``bridgeOut`` locks/burns the tokens on the source chain (ERC-20 tokens
   need an ``approve`` first — handled automatically).
4. Bool Network's relayer delivers the message; the destination bridge
   releases/mints to your recipient. **No destination-side action needed.**

This script bridges a small amount of USDC and then confirms arrival by
watching the destination bridge's ``BridgeIn`` event directly — no backend
status API required.

Run it:

    python examples/bridge_evm.py
"""

from __future__ import annotations

from decimal import Decimal

from _dotenv import load, optional, require

load()

from eth_account import Account

from deepx_sdk.bridge import BridgeApi


# ---------------------------------------------------------------------------
# 1. Configuration
#
# The DeepX side (RPC, chain id, bridge contract, sign API) needs no
# configuration. SDK development only: set the BRIDGE_SRC_* trio to point at
# the internal deployment.
# ---------------------------------------------------------------------------

SRC_RPC = optional("BRIDGE_SRC_RPC")
SRC_CHAIN_ID = optional("BRIDGE_SRC_CHAIN_ID")
SRC_BRIDGE = optional("BRIDGE_SRC")
SIGN_API_BASE = optional("SIGN_API_BASE")

DST_RPC = optional(
    "BRIDGE_DST_RPC", "https://ethereum-sepolia-rpc.publicnode.com"
)
DST_CHAIN_ID = int(optional("BRIDGE_DST_CHAIN_ID", "11155111"))
DST_BRIDGE = optional(
    "BRIDGE_DST", "0x70e6adc5c6c2f131b32ce8347876e6c1af4f65e8"
)

PRIVATE_KEY = require("PRIVATE_KEY")
AMOUNT_USDC = Decimal(optional("BRIDGE_AMOUNT_USDC", "1"))
USDC_TOKEN_ID = 3  # ETH=1, USDT=2, USDC=3, ... (see BRIDGE_TOKEN_MAP)


# ---------------------------------------------------------------------------
# 2. Bridge out from the source chain
# ---------------------------------------------------------------------------

sender = Account.from_key(PRIVATE_KEY).address
recipient = optional("BRIDGE_RECIPIENT", sender)  # defaults to sender
amount = int(AMOUNT_USDC * 10**6)  # USDC has 6 decimals on both chains here

dst = BridgeApi(
    rpc_url=DST_RPC,
    chain_id=DST_CHAIN_ID,
    contract_address=DST_BRIDGE,
)

# Blank values fall back to the built-in deployment.
src = BridgeApi(
    private_key=PRIVATE_KEY,
    rpc_url=SRC_RPC,
    chain_id=int(SRC_CHAIN_ID) if SRC_CHAIN_ID.strip() else None,
    contract_address=SRC_BRIDGE,
)

# record before bridging so the arrival event can never be missed
watch_from_block = dst.latest_block() - 20

info = src.get_token_info(USDC_TOKEN_ID)
if int(info["token"], 16) == 0:
    raise SystemExit("USDC (token id 3) is not registered on the source bridge")
print(f"source USDC: {info['token']} (decimals={info['local_decimal']})")

fee = src.get_bridge_fee(
    dst_chain_id=DST_CHAIN_ID,
    amount=amount,
    dst_recipient=recipient,
    token_id=USDC_TOKEN_ID,
)
print(f"bridge fee: {fee} wei (source gas token)")

print(f"bridging {AMOUNT_USDC} USDC: {sender} -> {recipient} on chain {DST_CHAIN_ID}")
result = src.bridge_out_with_sign(
    dst_chain_id=DST_CHAIN_ID,
    amount=amount,
    dst_recipient=recipient,
    token_id=USDC_TOKEN_ID,
    sign_api_base=SIGN_API_BASE or None,
    auto_approve=True,  # sends approve(tx) first when allowance is insufficient
    wait=False,         # we watch the destination chain ourselves below
)
print(f"approve tx: {result['approve_tx_hash']}")
print(f"bridge tx:  {result['tx_hash']}")


# ---------------------------------------------------------------------------
# 3. Confirm arrival on the destination chain
#
# The relayer usually delivers within a minute or two. ``wait_bridge_in``
# polls ``eth_getLogs`` for the BridgeIn event paid to ``recipient``.
# ---------------------------------------------------------------------------

print("waiting for BridgeIn on the destination chain ...")
event = dst.wait_bridge_in(
    recipient=recipient,
    from_block=watch_from_block,
    timeout_s=30 * 60,
    interval_s=15,
)
print("arrived!")
print(f"  dst tx:  {event['tx_hash']}")
print(f"  block:   {event['block_number']}")
print(f"  tokenId: {event['token_id']}  amount: {event['amount']}")
