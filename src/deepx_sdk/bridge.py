from __future__ import annotations

import json
import re
import secrets
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN
from typing import Any, Callable, Mapping, Optional

from ._abi import decode_abi, encode_call, normalize_address
from ._errors import RESTError
from ._evm import evm_call
from ._native import build_signed_tx
from ._native_py import _rpc_call
from ._network import network_config, resolve_net
from ._rpc_transport import DEFAULT_USER_AGENT
from ._types import TxResult
from .api import ApiClient

# topic0 of MultiTokenBridge's BridgeIn(bytes32,uint256,uint256,address).
BRIDGE_IN_EVENT_TOPIC = (
    "0xd27dacda04d3d9b76b4b91db5d3aae546abc65744db0169e7d13ac4891b0bbd6"
)
from .units import from_base_unit, to_base_unit

API_VERSION = "/internal/v1"

BITCOIN_MAINNET_CHAIN_ID = 2693367830
BITCOIN_TESTNET_CHAIN_ID = 271847360

# rbool-bridge-backend enums/web3.rs: Web3Network::Solana / SolanaDevnet
SOLANA_MAINNET_CHAIN_ID = 4221170919
SOLANA_DEVNET_CHAIN_ID = 2479745243

BITCOIN_TESTNET_ADDRESS_TYPES = {
    "p2tr": 1,
    "p2wpkh": 2,
    "p2sh": 3,
    "p2pkh": 4,
}

BITCOIN_TESTNET_FALLBACKS = {
    "depositAddress": "tb1pse65hw026ugeww9p9kq4ex4zxjyudrahj0996m599t862rep74kqqyrqg5",
    "withdrawContract": "0x4fd3334d42abb1fea0fb5feebd70d633d27f5ed8",
    "explorer": "https://mempool.space/testnet",
}

BRIDGE_TOKEN_MAP = {
    "ETH": 1,
    "USDT": 2,
    "USDC": 3,
    "DAI": 4,
    "BNB": 5,
    "OKB": 6,
    "SOL": 7,
    "BTC": 82,
}

SOLANA_TO_ETH_BRIDGE_PAYLOAD_LENGTH = 160
_BITCOIN_CHAIN_IDS = {
    str(BITCOIN_MAINNET_CHAIN_ID),
    str(BITCOIN_TESTNET_CHAIN_ID),
}
_SIGN_BRIDGE_OUT_BYTES_PATH = "sign-bridge/sign-bridge-out/bytes"
_SIGN_BRIDGE_OUT_BYTES32_PATH = "sign-bridge/sign-bridge-out/bytes32"
_BRIDGE_STATUS_VERIFYING = "Verifying"
_BRIDGE_HISTORY_STATUS_PENDING = "Pending"


@dataclass(frozen=True)
class BridgeFeeQuote:
    fee: int
    decimals: int
    symbol: str
    request_key: str | None = None


def _network_value(network: Any, *keys: str) -> Any:
    if network is None:
        return None
    if isinstance(network, Mapping):
        for key in keys:
            if key in network:
                return network[key]
    for key in keys:
        if hasattr(network, key):
            return getattr(network, key)
    return None


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _normalize_hex_bytes(value: str | bytes | bytearray | memoryview | None) -> bytes:
    if value is None:
        return b""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    raw = str(value).strip()
    if not raw:
        return b""
    if raw.startswith(("0x", "0X")):
        raw = raw[2:]
    if raw == "":
        return b""
    if len(raw) % 2 != 0:
        if re.fullmatch(r"[0-9a-fA-F]+", raw):
            raise ValueError("hex value must have an even length")
        return raw.encode("utf-8")
    if not re.fullmatch(r"[0-9a-fA-F]+", raw):
        return raw.encode("utf-8")
    return bytes.fromhex(raw)


def _normalize_bytes32_value(value: str | bytes | bytearray | memoryview | None) -> bytes:
    raw = _normalize_hex_bytes(value)
    if len(raw) > 32:
        raise ValueError("value must be at most 32 bytes")
    return raw.rjust(32, b"\x00")


def _zero_pad_value(value: str | bytes | bytearray | memoryview | Any, size: int = 32) -> str:
    raw = _public_key_bytes(value)
    if len(raw) > size:
        raise ValueError(f"value must be at most {size} bytes")
    return "0x" + raw.rjust(size, b"\x00").hex()


def _public_key_string(value: Any) -> str:
    if isinstance(value, str):
        return value
    for attr in ("to_string", "toString"):
        method = getattr(value, attr, None)
        if callable(method):
            return str(method())
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "0x" + bytes(value).hex()
    return str(value)


def _public_key_bytes(value: Any) -> bytes:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    for attr in ("to_buffer", "toBuffer"):
        method = getattr(value, attr, None)
        if callable(method):
            return bytes(method())
    if isinstance(value, str):
        raw = value.strip()
        if raw.startswith(("0x", "0X")):
            return _normalize_hex_bytes(raw)
        if re.fullmatch(r"[0-9a-fA-F]+", raw) and len(raw) % 2 == 0:
            return bytes.fromhex(raw)
        return raw.encode("utf-8")
    if hasattr(value, "__bytes__"):
        return bytes(value)
    raise TypeError("value does not expose a usable byte representation")


def _response_message(payload: Any) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    for key in ("msg", "message", "error"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _is_bitcoin_chain_id(dst_chain_id: int | str) -> bool:
    return str(dst_chain_id).strip() in _BITCOIN_CHAIN_IDS


def _bridge_total_decimals(decimals: int | None = None) -> int:
    if decimals is None or int(decimals) <= 6:
        return 6
    return min(int(decimals), 8)


def _format_decimal_amount(value: Decimal, decimals: int) -> str:
    quant = Decimal(1).scaleb(-decimals)
    normalized = value.quantize(quant, rounding=ROUND_DOWN)
    rendered = format(normalized, ",f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58decode(value: str) -> bytes:
    num = 0
    for ch in value:
        num *= 58
        idx = _B58_ALPHABET.find(ch)
        if idx < 0:
            raise ValueError(f"invalid base58 character: {ch!r}")
        num += idx
    raw = num.to_bytes((num.bit_length() + 7) // 8, "big") if num else b""
    pad = len(value) - len(value.lstrip("1"))
    return b"\x00" * pad + raw


def solana_address_to_bytes32(address: str) -> str:
    """Solana address (base58, 32 bytes) -> 0x-prefixed bytes32 hex, for dst_recipient."""
    raw = _b58decode(address.strip())
    if len(raw) != 32:
        raise ValueError(f"solana address must decode to 32 bytes, got {len(raw)}")
    return "0x" + raw.hex()


def generate_bridge_salt() -> str:
    """Random 32-byte salt (0x hex). The contract prevents replay via usedSalt[digest]; use a fresh salt per bridge."""
    return "0x" + secrets.token_hex(32)


def _evm_address_from_key(private_key: str) -> str:
    from eth_account import Account

    return str(Account.from_key(private_key).address)


def get_sign_bridge_out_url(base_url: str, dst_chain_id: int | str) -> str:
    if _is_blank(base_url):
        raise ValueError("Backend API base URL is not configured for bridge signature request.")
    path = _SIGN_BRIDGE_OUT_BYTES_PATH if _is_bitcoin_chain_id(dst_chain_id) else _SIGN_BRIDGE_OUT_BYTES32_PATH
    return f"{str(base_url).rstrip('/')}{API_VERSION}/{path}"


def _get_request_dst_chain_id(body: Mapping[str, Any], dst_chain_id: int | str | None = None) -> int | str:
    request_dst_chain_id = dst_chain_id
    if request_dst_chain_id is None:
        params = body.get("params")
        if isinstance(params, Mapping):
            request_dst_chain_id = params.get("dst_chain_id")
    if _is_blank(request_dst_chain_id):
        raise ValueError("Destination chain ID is required for bridge signature request.")
    return request_dst_chain_id


def create_sign_bridge_out_request_body(
    body: Mapping[str, Any],
    dst_chain_id: int | str,
) -> dict[str, Any]:
    if not _is_bitcoin_chain_id(dst_chain_id):
        return dict(body)

    params = body.get("params")
    if not isinstance(params, Mapping):
        return dict(body)

    return {
        **dict(body),
        "params": {
            "amount": params.get("amount"),
            "sender": params.get("sender"),
            "dst_recipient": params.get("dst_recipient"),
            "refund_address": params.get("refund_address"),
            "salt": params.get("salt"),
        },
    }


def fetch_sign_bridge_out_signature(
    base_url: str,
    body: Mapping[str, Any],
    dst_chain_id: int | str | None = None,
    timeout: int = 30,
    user_agent: str = DEFAULT_USER_AGENT,
) -> dict[str, Any]:
    request_dst_chain_id = _get_request_dst_chain_id(body, dst_chain_id)
    fetch_url = get_sign_bridge_out_url(base_url, request_dst_chain_id)
    request_body = create_sign_bridge_out_request_body(body, request_dst_chain_id)

    payload_bytes = json.dumps(request_body).encode("utf-8")
    req = urllib.request.Request(
        fetch_url,
        data=payload_bytes,
        method="POST",
        headers={
            "Content-Type": "application/json",
            # the gateway CDN rejects urllib's default User-Agent with 403
            "User-Agent": user_agent,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raw = ""
        if exc.fp:
            try:
                raw = exc.read().decode("utf-8", errors="replace")
            except Exception:
                raw = ""
        payload: Any = {}
        if raw:
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {}
        message = _response_message(payload) or exc.reason or "Bridge signature request failed"
        raise RESTError(
            status_code=exc.code,
            message=message,
            code=payload.get("code") if isinstance(payload, Mapping) else None,
            error_type=payload.get("errorType") if isinstance(payload, Mapping) else None,
        ) from exc
    except urllib.error.URLError as exc:
        raise RESTError(status_code=None, message=f"Bridge signature request failed: {exc}") from exc

    payload: Any = {}
    if raw:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {}

    message = _response_message(payload)
    if not isinstance(payload, Mapping) or not isinstance(payload.get("signature"), str) or not payload.get("signature"):
        raise ValueError(message or "Bridge signature response did not include a signature.")
    return dict(payload)


def normalize_bridge_amount(value: str | int | float | Decimal | None) -> str:
    return str(value or "0").replace(",", "").strip() or "0"


def parse_bridge_amount(value: str | int | float | Decimal | None, decimals: int | None = None) -> int:
    return to_base_unit(normalize_bridge_amount(value), 18 if decimals is None else int(decimals))


def add_bridge_fee(amount: int, fee: int | None = None) -> int:
    return int(amount) + int(fee or 0)


def format_bridge_fee(quote: BridgeFeeQuote | None) -> str:
    if quote is None:
        return "0"
    amount = from_base_unit(int(quote.fee), int(quote.decimals))
    return f"{_format_decimal_amount(amount, _bridge_total_decimals(quote.decimals))} {quote.symbol}"


def format_bridge_total_amount(
    value: str | int | float | Decimal | None,
    quote: BridgeFeeQuote | None,
) -> str:
    if quote is None:
        return "N/A"
    total = add_bridge_fee(parse_bridge_amount(value, quote.decimals), quote.fee)
    amount = from_base_unit(total, int(quote.decimals))
    return f"{_format_decimal_amount(amount, _bridge_total_decimals(quote.decimals))} {quote.symbol}"


def get_bridge_total_amount(
    value: str | int | float | Decimal | None,
    quote: BridgeFeeQuote | None,
) -> str:
    if quote is None:
        return normalize_bridge_amount(value)
    total = add_bridge_fee(parse_bridge_amount(value, quote.decimals), quote.fee)
    rendered = format(from_base_unit(total, quote.decimals), "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def is_valid_bitcoin_address(address: str | None = None) -> bool:
    if not address:
        return False
    return bool(re.match(r"^(bc1|[13]|tb1|[mn2])[a-zA-HJ-NP-Z0-9]{25,62}$", address))


def get_bitcoin_address_network(address: str) -> str:
    if address.startswith(("bc1", "1", "3")):
        return "livenet"
    return "testnet"


def get_bitcoin_address_type(address: str) -> str:
    if address.startswith(("bc1p", "tb1p")):
        return "p2tr"
    if address.startswith(("bc1q", "tb1q")):
        return "p2wpkh"
    if address.startswith(("2", "3")):
        return "p2sh"
    return "p2pkh"


def format_bitcoin_address(address: str) -> str:
    address_type = get_bitcoin_address_type(address)
    index = BITCOIN_TESTNET_ADDRESS_TYPES[address_type]
    padding = max(0, 63 - len(address))
    body = bytearray(1 + padding + len(address))
    body[0] = index
    body[1 + padding :] = address.encode("utf-8")
    return "0x" + bytes(body).hex()


def to_satoshis(amount: str | int | float) -> int:
    value = str(amount)
    whole, _, fraction = value.partition(".")
    normalized_fraction = f"{fraction}00000000"[:8]
    return int(f"{whole or '0'}{normalized_fraction}")


def _parse_satoshi_value(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def get_bitcoin_balance_satoshis(balance: Any) -> int:
    direct = _parse_satoshi_value(balance)
    if direct is not None:
        return direct
    if not isinstance(balance, Mapping):
        return 0
    return (
        _parse_satoshi_value(balance.get("satoshis"))
        or _parse_satoshi_value(balance.get("total"))
        or _parse_satoshi_value(balance.get("confirmed"))
        or 0
    )


def from_satoshis(amount: int) -> str:
    sign = "-" if amount < 0 else ""
    normalized = str(abs(int(amount))).rjust(9, "0")
    whole = normalized[:-8]
    fraction = normalized[-8:].rstrip("0")
    return f"{sign}{whole}{('.' + fraction) if fraction else ''}"


def generate_bitcoin_deposit_data(
    *,
    chain_id: int,
    receiver: str,
    consumer_data: str = "0x",
) -> str:
    receiver_hex = receiver[2:] if receiver.startswith(("0x", "0X")) else receiver
    consumer_data_hex = consumer_data[2:] if consumer_data.startswith(("0x", "0X")) else consumer_data
    if len(receiver_hex) != 64:
        raise ValueError("receiver must be exactly 32 bytes")
    if len(consumer_data_hex) % 2 != 0:
        raise ValueError("consumerData must be an even-length hex string")
    consumer_data_length = len(consumer_data_hex) // 2
    if consumer_data_length > 0xFFFF:
        raise ValueError("consumerData is too large")
    return (
        f"{int(chain_id):08x}"
        f"{receiver_hex.lower()}"
        f"{consumer_data_length:04x}"
        f"{consumer_data_hex.lower()}"
    )


def normalize_bitcoin_deposit_utxos(
    *,
    utxos: list[Mapping[str, Any]],
    payment_address: str,
    payment_pubkey: str,
) -> list[dict[str, Any]]:
    def has_protected_assets(utxo: Mapping[str, Any]) -> bool:
        return bool(utxo.get("inscriptions") or utxo.get("atomicals") or utxo.get("runes"))

    normalized: list[dict[str, Any]] = []
    for utxo in utxos:
        if has_protected_assets(utxo):
            continue
        normalized.append(
            {
                "txId": utxo["txid"],
                "outputIndex": utxo["vout"],
                "satoshis": utxo["satoshis"],
                "scriptPk": utxo["scriptPk"],
                "addressType": utxo["addressType"],
                "address": payment_address,
                "pubkey": payment_pubkey,
                "ords": [],
            }
        )
    return normalized


def get_solana_bridge_fee_payload_length(consumer_data: str | bytes | bytearray | memoryview | None = None) -> int:
    return SOLANA_TO_ETH_BRIDGE_PAYLOAD_LENGTH + len(_normalize_hex_bytes(consumer_data))


def get_bridge_token_id(symbol: str | None = None) -> int:
    token_symbol = (symbol or "USDC").strip().upper()
    if token_symbol not in BRIDGE_TOKEN_MAP:
        raise ValueError(f"unknown bridge token symbol: {symbol!r}")
    return BRIDGE_TOKEN_MAP[token_symbol]


def get_withdraw_dst_recipient(
    *,
    target_network: Any,
    token_symbol: str | None = None,
    target_token_address: str | None = None,
    evm_address: str | None = None,
    solana_public_key: Any | None = None,
    bitcoin_address: str | None = None,
    get_solana_token_account_address: Callable[..., Any] | None = None,
) -> str:
    network_type = str(_network_value(target_network, "networkType", "network_type") or "").strip().lower()
    network_name = _network_value(target_network, "name") or "target network"

    if network_type == "solana":
        if solana_public_key is None:
            raise ValueError("Solana wallet not connected")
        if (token_symbol or "").strip().lower() == "sol":
            return _zero_pad_value(_public_key_bytes(solana_public_key), 32)
        if not target_token_address:
            raise ValueError(f"Token {token_symbol or ''} not found on {network_name}")
        resolver = get_solana_token_account_address
        if resolver is None:
            raise ValueError("Solana token account resolver is required")
        token_account = resolver(
            target_network=target_network,
            mint_address=target_token_address,
            owner_address=_public_key_string(solana_public_key),
        )
        return _zero_pad_value(_public_key_bytes(token_account), 32)

    if network_type == "bitcoin":
        if not bitcoin_address:
            raise ValueError("Bitcoin wallet not connected")
        return format_bitcoin_address(bitcoin_address)

    if not evm_address:
        raise ValueError("Wallet is not connected.")
    return _zero_pad_value(_normalize_hex_bytes(evm_address), 32)


def get_bridge_status_check_hash(*values: str | None) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def get_bridge_status_check_src_hash(record: Mapping[str, Any]) -> str | None:
    return get_bridge_status_check_hash(
        record.get("swapRecordSrcChainHash"),
        record.get("srcHash"),
        record.get("srcChainHash"),
    )


def get_bridge_status_check_dst_hash(record: Mapping[str, Any]) -> str | None:
    return get_bridge_status_check_hash(
        record.get("swapRecordDstChainHash"),
        record.get("dstHash"),
        record.get("dstChainHash"),
    )


def create_bridge_status_check_update(record: Mapping[str, Any], src_hash: str) -> dict[str, Any]:
    update: dict[str, Any] = {
        "swapRecordSrcChainHash": src_hash,
        "swapRecordStatus": _BRIDGE_STATUS_VERIFYING,
    }
    dst_hash = get_bridge_status_check_dst_hash(record)
    if dst_hash:
        update["swapRecordDstChainHash"] = dst_hash
    return update


def apply_bridge_history_subaccount_address(
    tx: Mapping[str, Any],
    app_chain_id: str | int,
    subaccount_address: str | None = None,
) -> Mapping[str, Any]:
    if not subaccount_address:
        return tx
    result = dict(tx)
    if str(tx.get("swapRecordSrcChainId")) == str(app_chain_id):
        result["swapRecordSrcUserAddress"] = subaccount_address
    if str(tx.get("swapRecordDstChainId")) == str(app_chain_id):
        result["swapRecordDstUserAddress"] = subaccount_address
    return result


def needs_bridge_history_supplement(tx: Mapping[str, Any], app_chain_id: str | int) -> bool:
    return (
        str(tx.get("swapRecordSrcChainId")) == str(app_chain_id)
        and (not tx.get("swapRecordDstChainHash") or not tx.get("swapRecordDstUserAddress"))
    )


def merge_bridge_history_supplement(
    tx: Mapping[str, Any],
    supplement: Mapping[str, Any] | None,
    app_chain_id: str | int,
    subaccount_address: str | None = None,
) -> dict[str, Any]:
    if supplement is None:
        return apply_bridge_history_subaccount_address(tx, app_chain_id, subaccount_address)
    merged = {
        **dict(supplement),
        **dict(tx),
        "swapRecordDstChainHash": tx.get("swapRecordDstChainHash") or supplement.get("swapRecordDstChainHash"),
        "swapRecordDstChainTime": tx.get("swapRecordDstChainTime") or supplement.get("swapRecordDstChainTime"),
        "swapRecordDstUserAddress": tx.get("swapRecordDstUserAddress") or supplement.get("swapRecordDstUserAddress"),
        "swapRecordStatus": tx.get("swapRecordStatus") or supplement.get("swapRecordStatus"),
    }
    return apply_bridge_history_subaccount_address(merged, app_chain_id, subaccount_address)


def merge_bridge_history_update(record: Mapping[str, Any], update: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(record)
    if "swapRecordDstChainHash" in update:
        merged["swapRecordDstChainHash"] = update["swapRecordDstChainHash"]
    if "swapRecordStatus" in update:
        merged["swapRecordStatus"] = update["swapRecordStatus"]
    return merged


@dataclass
class BridgeServiceClient:
    bridge_api_url: str
    api_key: str | None = None
    timeout: int = 30
    user_agent: str = DEFAULT_USER_AGENT
    _client: ApiClient = field(init=False, repr=False)

    def __post_init__(self) -> None:
        base_url = str(self.bridge_api_url).strip()
        if not base_url:
            raise ValueError("bridge_api_url is required")
        self.bridge_api_url = base_url
        self._client = ApiClient(
            base_url=base_url,
            timeout=self.timeout,
            user_agent=self.user_agent,
        )

    def _auth_headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}

    def check_transaction_statuses(self, check_infos: list[Mapping[str, Any]]) -> Any:
        return self._client.request(
            "POST",
            "/swap/swap-record:check",
            json_body={"checkInfos": check_infos},
            headers=self._auth_headers(),
        )

    def check_transaction_status(
        self,
        *,
        chain_id: int | str,
        src_chain_hash: str,
    ) -> dict[str, Any]:
        result = self.check_transaction_statuses([
            {
                "chainID": int(chain_id),
                "srcChainHash": [src_chain_hash],
            }
        ])
        normalized_hash = src_chain_hash.strip().lower()
        records = result.get("data") if isinstance(result, Mapping) else None
        if not isinstance(records, list):
            records = []
        record = next(
            (
                item
                for item in records
                if isinstance(item, Mapping)
                and (get_bridge_status_check_src_hash(item) or "").lower() == normalized_hash
            ),
            None,
        )
        if record is None:
            return {"stage": "confirming"}
        return {
            "record": record,
            "stage": "received" if get_bridge_status_check_dst_hash(record) else "verifying",
        }

    def list_records(
        self,
        *,
        page_no: int = 1,
        page_size: int = 20,
        user_address: str | None = None,
        src_chain_hash: str | None = None,
        bridge_no: str | None = None,
    ) -> Any:
        params: dict[str, Any] = {
            "pageNo": page_no,
            "pageSize": page_size,
        }
        if user_address:
            params["userAddress"] = user_address
        if src_chain_hash:
            params["srcChainHash"] = src_chain_hash
        if bridge_no:
            params["bridgeNo"] = bridge_no
        return self._client.request("GET", "/swap/swap-records", params=params, headers=self._auth_headers())


def _wait_bridge_received(
    *,
    bridge_api_url: str,
    src_chain_id: int,
    src_tx_hash: str,
    timeout_s: int,
    interval_s: float,
) -> dict[str, Any]:
    """Poll the rbool-bridge swap-record until the destination hash appears (stage == "received")."""
    client = BridgeServiceClient(bridge_api_url=bridge_api_url)
    deadline = time.monotonic() + timeout_s
    status: dict[str, Any] = {"stage": "confirming"}
    while time.monotonic() < deadline:
        status = client.check_transaction_status(
            chain_id=src_chain_id,
            src_chain_hash=src_tx_hash,
        )
        if status.get("stage") == "received":
            return status
        time.sleep(interval_s)
    raise TimeoutError(
        f"bridge destination not released within {timeout_s}s (last stage: {status.get('stage')})"
    )


@dataclass
class BridgeApi:
    """MultiTokenBridge client.

    With no arguments it targets the default DeepX network — RPC endpoint,
    chain id, and bridge contract all resolve from `net`. Pass explicit
    `rpc_url` / `chain_id` / `contract_address` to override (e.g. for the
    external side of a bridge pair such as Sepolia).
    """

    rpc_url: str | None = None
    chain_id: int | None = None
    contract_address: str | None = None
    private_key: str | None = None
    net: str | None = None

    def __post_init__(self) -> None:
        self.net = resolve_net(self.net)
        config = network_config(self.net)
        if _is_blank(self.rpc_url):
            self.rpc_url = config.evm_rpc_url
        if self.chain_id is None:
            self.chain_id = config.chain_id
        if _is_blank(self.contract_address):
            self.contract_address = config.bridge_contract

    def get_bridge_fee(
        self,
        *,
        dst_chain_id: int,
        amount: int,
        dst_recipient: str | bytes | bytearray | memoryview,
        token_id: int | None = None,
        symbol: str | None = None,
        custom_data: str | bytes | bytearray | memoryview = "0x",
    ) -> int:
        data = encode_call(
            "getBridgeFee(uint256,uint32,uint256,bytes32,bytes)",
            ["uint256", "uint32", "uint256", "bytes32", "bytes"],
            [
                get_bridge_token_id(symbol) if token_id is None else int(token_id),
                _validate_uint32(dst_chain_id),
                int(amount),
                _normalize_bytes32_value(dst_recipient),
                _normalize_hex_bytes(custom_data),
            ],
        )
        raw = evm_call(self.rpc_url, normalize_address(self.contract_address), data)
        (fee,) = decode_abi(["uint256"], raw)
        return int(fee)

    def _send_call(
        self,
        *,
        data: bytes,
        value: int,
        private_key: str | None,
        chain_id: int | None,
        gas_limit: int | None,
        max_fee_per_gas: int | None,
        max_priority_fee_per_gas: int | None,
        use_legacy: bool,
        nonce_ms: int | None,
        contract_address: str | None = None,
    ) -> TxResult:
        resolved_private_key = private_key or self.private_key
        if _is_blank(resolved_private_key):
            raise ValueError("private_key is required")
        target = self.contract_address if contract_address is None else contract_address
        signed = build_signed_tx(
            evm_rpc_url=self.rpc_url,
            private_key=str(resolved_private_key),
            precompile_address=normalize_address(target),
            data=data,
            value=int(value),
            chain_id=self.chain_id if chain_id is None else int(chain_id),
            gas_limit=gas_limit,
            max_fee_per_gas=max_fee_per_gas,
            max_priority_fee_per_gas=max_priority_fee_per_gas,
            use_legacy=use_legacy,
            nonce_ms=nonce_ms,
            use_timestamp_nonce=False,
        )
        tx_hash = _rpc_call(self.rpc_url, "eth_sendRawTransaction", [signed.signed_tx])
        return TxResult(tx_hash=str(tx_hash), event=None)

    def bridge_out(
        self,
        *,
        dst_chain_id: int,
        amount: int,
        dst_recipient: str | bytes | bytearray | memoryview,
        refund_address: str,
        salt: str | bytes | bytearray | memoryview,
        signature: str | bytes | bytearray | memoryview,
        token_id: int | None = None,
        symbol: str | None = None,
        custom_data: str | bytes | bytearray | memoryview = "0x",
        is_native: bool = False,
        fee: int | None = None,
        private_key: str | None = None,
        chain_id: int | None = None,
        gas_limit: int | None = None,
        max_fee_per_gas: int | None = None,
        max_priority_fee_per_gas: int | None = None,
        use_legacy: bool = False,
        nonce_ms: int | None = None,
    ) -> TxResult:
        resolved_token_id = get_bridge_token_id(symbol) if token_id is None else int(token_id)
        resolved_fee = (
            self.get_bridge_fee(
                dst_chain_id=dst_chain_id,
                amount=amount,
                dst_recipient=dst_recipient,
                token_id=resolved_token_id,
                custom_data=custom_data,
            )
            if fee is None
            else int(fee)
        )

        data = encode_call(
            "bridgeOut(uint32,uint256,uint256,bytes32,address,bytes32,bytes,bytes)",
            ["uint32", "uint256", "uint256", "bytes32", "address", "bytes32", "bytes", "bytes"],
            [
                _validate_uint32(dst_chain_id),
                resolved_token_id,
                int(amount),
                _normalize_bytes32_value(dst_recipient),
                normalize_address(refund_address),
                _normalize_bytes32_value(salt),
                _normalize_hex_bytes(custom_data),
                _normalize_hex_bytes(signature),
            ],
        )
        # MultiTokenBridge: gas token -> msg.value >= amount + fee, otherwise >= fee
        value = int(amount) + resolved_fee if is_native else resolved_fee
        return self._send_call(
            data=data,
            value=value,
            private_key=private_key,
            chain_id=chain_id,
            gas_limit=gas_limit,
            max_fee_per_gas=max_fee_per_gas,
            max_priority_fee_per_gas=max_priority_fee_per_gas,
            use_legacy=use_legacy,
            nonce_ms=nonce_ms,
        )

    def approve_erc20(
        self,
        *,
        token_address: str,
        amount: int,
        spender: str | None = None,
        private_key: str | None = None,
        chain_id: int | None = None,
        gas_limit: int | None = None,
        max_fee_per_gas: int | None = None,
        max_priority_fee_per_gas: int | None = None,
        use_legacy: bool = False,
        nonce_ms: int | None = None,
    ) -> TxResult:
        # Both bridge contracts pull tokens via safeTransferFrom(msg.sender), so the
        # owner EOA must approve the bridge/channel contract first.
        resolved_spender = self.contract_address if spender is None else spender
        data = encode_call(
            "approve(address,uint256)",
            ["address", "uint256"],
            [normalize_address(resolved_spender), int(amount)],
        )
        return self._send_call(
            data=data,
            value=0,
            private_key=private_key,
            chain_id=chain_id,
            gas_limit=gas_limit,
            max_fee_per_gas=max_fee_per_gas,
            max_priority_fee_per_gas=max_priority_fee_per_gas,
            use_legacy=use_legacy,
            nonce_ms=nonce_ms,
            contract_address=token_address,
        )

    def get_token_info(self, token_id: int) -> dict[str, Any]:
        """tokenIdToInfo(tokenId). token == 0x0 means the tokenId is not registered."""
        data = encode_call("tokenIdToInfo(uint256)", ["uint256"], [int(token_id)])
        raw = evm_call(self.rpc_url, normalize_address(self.contract_address), data)
        is_native, is_gas, token, common_dec, local_dec, fixed = decode_abi(
            ["bool", "bool", "address", "uint8", "uint8", "bool"], raw
        )
        return {
            "is_native_asset": bool(is_native),
            "is_gas_token": bool(is_gas),
            "token": str(token),
            "common_decimal": int(common_dec),
            "local_decimal": int(local_dec),
            "is_config_fixed": bool(fixed),
        }

    def get_allowance(self, *, token_address: str, owner: str) -> int:
        """ERC20 allowance of owner to the bridge contract (self.contract_address)."""
        data = encode_call(
            "allowance(address,address)",
            ["address", "address"],
            [normalize_address(owner), normalize_address(self.contract_address)],
        )
        raw = evm_call(self.rpc_url, normalize_address(token_address), data)
        (allowance,) = decode_abi(["uint256"], raw)
        return int(allowance)

    def bridge_out_with_sign(
        self,
        *,
        dst_chain_id: int,
        amount: int,
        dst_recipient: str | bytes | bytearray | memoryview,
        sign_api_base: str | None = None,
        token_id: int | None = None,
        symbol: str | None = None,
        sender: str | None = None,
        refund_address: str | None = None,
        salt: str | bytes | bytearray | memoryview | None = None,
        custom_data: str | bytes | bytearray | memoryview = "0x",
        domain_name: str = "Channel",
        domain_version: str = "1",
        verifying_contract: str | None = None,
        fee: int | None = None,
        auto_approve: bool = True,
        wait: bool = False,
        bridge_api_url: str | None = None,
        wait_timeout_s: int = 600,
        wait_interval_s: float = 10.0,
        private_key: str | None = None,
        chain_id: int | None = None,
        gas_limit: int | None = None,
        max_fee_per_gas: int | None = None,
        max_priority_fee_per_gas: int | None = None,
        use_legacy: bool = False,
        nonce_ms: int | None = None,
    ) -> dict[str, Any]:
        """One-shot bridge: sign -> approve (if needed) -> bridgeOut -> (optional) wait for release.

        - sender defaults to the address derived from private_key; it is both the
          signed sender and the EOA that must submit the transaction
        - salt defaults to a random value
        - token address / isGasToken come from the on-chain tokenIdToInfo;
          unregistered token ids raise immediately
        - for non-gas tokens with auto_approve=True, an approve tx is sent first
          when the allowance is insufficient
        - wait=True requires bridge_api_url (rbool-bridge backend) and polls the
          swap-record until the destination hash appears; raises TimeoutError on timeout
        """
        resolved_private_key = private_key or self.private_key
        if _is_blank(resolved_private_key):
            raise ValueError("private_key is required")
        # Defaults to the network's REST API; override for custom deployments.
        resolved_sign_api_base = sign_api_base or network_config(self.net).api_base_url
        if sender is None:
            sender = _evm_address_from_key(str(resolved_private_key))
        resolved_refund = refund_address or sender
        resolved_token_id = get_bridge_token_id(symbol) if token_id is None else int(token_id)
        resolved_contract = self.contract_address if verifying_contract is None else verifying_contract
        resolved_salt = generate_bridge_salt() if salt is None else salt
        salt32 = _normalize_bytes32_value(resolved_salt)
        recipient32 = _normalize_bytes32_value(dst_recipient)

        token_info = self.get_token_info(resolved_token_id)
        if int(token_info["token"], 16) == 0:
            raise ValueError(f"token_id {resolved_token_id} is not registered on the bridge")
        is_gas_token = token_info["is_gas_token"]

        # 1) authorizer signature
        sign_body = {
            "domain": {
                "name": domain_name,
                "version": domain_version,
                "chain_id": self.chain_id if chain_id is None else int(chain_id),
                "verifying_contract": resolved_contract,
            },
            "params": {
                "dst_chain_id": int(dst_chain_id),
                "token_id": str(resolved_token_id),
                "amount": str(int(amount)),
                "sender": sender,
                "dst_recipient": "0x" + recipient32.hex(),
                "refund_address": resolved_refund,
                "salt": "0x" + salt32.hex(),
                "custom_data_hex": custom_data if isinstance(custom_data, str) else "0x" + _normalize_hex_bytes(custom_data).hex(),
            },
        }
        sign_result = fetch_sign_bridge_out_signature(resolved_sign_api_base, sign_body, int(dst_chain_id))
        if sign_result.get("matches_authorizer") is False:
            raise ValueError("sign-bridge response does not match the authorizer key")

        tx_opts = dict(
            private_key=str(resolved_private_key),
            chain_id=chain_id,
            gas_limit=gas_limit,
            max_fee_per_gas=max_fee_per_gas,
            max_priority_fee_per_gas=max_priority_fee_per_gas,
            use_legacy=use_legacy,
            nonce_ms=nonce_ms,
        )

        # 2) approve (not needed for gas tokens)
        approve_tx_hash: str | None = None
        if not is_gas_token and auto_approve:
            allowance = self.get_allowance(token_address=token_info["token"], owner=sender)
            if allowance < int(amount):
                approve_tx = self.approve_erc20(
                    token_address=token_info["token"],
                    amount=int(amount),
                    **tx_opts,
                )
                approve_tx_hash = approve_tx.tx_hash

        # 3) bridgeOut
        tx = self.bridge_out(
            dst_chain_id=dst_chain_id,
            amount=amount,
            dst_recipient=recipient32,
            refund_address=resolved_refund,
            salt=salt32,
            signature=sign_result["signature"],
            token_id=resolved_token_id,
            custom_data=custom_data,
            is_native=is_gas_token,
            fee=fee,
            **tx_opts,
        )

        result: dict[str, Any] = {
            "tx_hash": tx.tx_hash,
            "approve_tx_hash": approve_tx_hash,
            "sender": sender,
            "salt": "0x" + salt32.hex(),
            "signature": sign_result["signature"],
            "digest": sign_result.get("digest"),
        }

        # 4) optional: wait for the destination release
        if wait:
            if _is_blank(bridge_api_url):
                raise ValueError("bridge_api_url is required when wait=True")
            result["bridge_status"] = _wait_bridge_received(
                bridge_api_url=str(bridge_api_url),
                src_chain_id=self.chain_id if chain_id is None else int(chain_id),
                src_tx_hash=tx.tx_hash,
                timeout_s=wait_timeout_s,
                interval_s=wait_interval_s,
            )
        return result

    def check_transaction(self, tx_hash: str) -> bool:
        receipt = _rpc_call(self.rpc_url, "eth_getTransactionReceipt", [tx_hash])
        if not receipt:
            return False
        if not isinstance(receipt, Mapping):
            return False
        status = receipt.get("status")
        return status in (1, "0x1", "0x01", True)

    def latest_block(self) -> int:
        """eth_blockNumber on this chain."""
        return int(_rpc_call(self.rpc_url, "eth_blockNumber", []), 16)

    def get_bridge_in_logs(
        self,
        *,
        recipient: str | None = None,
        from_block: int = 0,
        to_block: int | str = "latest",
    ) -> list[dict[str, Any]]:
        """Fetch and decode `BridgeIn` events emitted by this chain's bridge.

        Destination-side delivery is done by the Bool Network relayer, not the
        user, so after `bridge_out` these logs are how you confirm arrival.
        `recipient` (EVM address) filters to a single receiver.
        """
        topics: list[Any] = [BRIDGE_IN_EVENT_TOPIC]
        if recipient is not None:
            topics.append("0x" + normalize_address(recipient)[2:].lower().rjust(64, "0"))
        logs = _rpc_call(
            self.rpc_url,
            "eth_getLogs",
            [
                {
                    "address": normalize_address(self.contract_address),
                    "topics": topics,
                    "fromBlock": hex(int(from_block)),
                    "toBlock": to_block if isinstance(to_block, str) else hex(int(to_block)),
                }
            ],
        )
        results: list[dict[str, Any]] = []
        for log in logs or []:
            tx_id, token_id, amount = decode_abi(
                ["bytes32", "uint256", "uint256"], bytes.fromhex(log["data"][2:])
            )
            results.append(
                {
                    "tx_hash": str(log["transactionHash"]),
                    "block_number": int(log["blockNumber"], 16),
                    "tx_unique_identification": "0x" + bytes(tx_id).hex(),
                    "token_id": int(token_id),
                    "amount": int(amount),
                }
            )
        return results

    def wait_bridge_in(
        self,
        *,
        recipient: str | None = None,
        from_block: int = 0,
        timeout_s: float = 1800.0,
        interval_s: float = 15.0,
    ) -> dict[str, Any]:
        """Poll `get_bridge_in_logs` until a matching BridgeIn appears.

        Transient RPC errors are retried until the timeout; raises TimeoutError
        when no event arrives within `timeout_s`.
        """
        deadline = time.monotonic() + float(timeout_s)
        while True:
            try:
                logs = self.get_bridge_in_logs(recipient=recipient, from_block=from_block)
            except Exception:
                logs = []
            if logs:
                return logs[0]
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"no BridgeIn event within {timeout_s}s "
                    f"(bridge={self.contract_address}, recipient={recipient})"
                )
            time.sleep(float(interval_s))


@dataclass
class BtcChannelApi(BridgeApi):
    """BTCCChannel (BTC side) step 2: withdraw(uint256,bytes,address,bytes32,bytes).

    Unlike MultiTokenBridge: recipient is 64-byte bytes (encoded BTC address)
    and msg.value must equal channelFee exactly (not >=).
    """

    def convert_amount_to_l1(self, amount: int) -> int:
        data = encode_call("convertAmountToL1(uint256)", ["uint256"], [int(amount)])
        raw = evm_call(self.rpc_url, normalize_address(self.contract_address), data)
        (converted,) = decode_abi(["uint256"], raw)
        return int(converted)

    def get_total_withdraw_fee(
        self,
        *,
        amount_l1: int,
        recipient: str | bytes | bytearray | memoryview,
    ) -> tuple[int, int]:
        """Returns (channelFee, withdrawFee). amount_l1 is the convertAmountToL1 output."""
        data = encode_call(
            "getTotalWithdrawFee(uint256,bytes)",
            ["uint256", "bytes"],
            [int(amount_l1), _normalize_hex_bytes(recipient)],
        )
        raw = evm_call(self.rpc_url, normalize_address(self.contract_address), data)
        channel_fee, withdraw_fee = decode_abi(["uint256", "uint256"], raw)
        return int(channel_fee), int(withdraw_fee)

    def btc_withdraw(
        self,
        *,
        amount: int,
        recipient: str | bytes | bytearray | memoryview,
        refund_address: str,
        salt: str | bytes | bytearray | memoryview,
        signature: str | bytes | bytearray | memoryview,
        channel_fee: int | None = None,
        private_key: str | None = None,
        chain_id: int | None = None,
        gas_limit: int | None = None,
        max_fee_per_gas: int | None = None,
        max_priority_fee_per_gas: int | None = None,
        use_legacy: bool = False,
        nonce_ms: int | None = None,
    ) -> TxResult:
        recipient_bytes = _normalize_hex_bytes(recipient)
        if len(recipient_bytes) != 64:
            raise ValueError("recipient must be exactly 64 bytes (format_bitcoin_address output)")
        resolved_channel_fee = (
            self.get_total_withdraw_fee(
                amount_l1=self.convert_amount_to_l1(amount),
                recipient=recipient_bytes,
            )[0]
            if channel_fee is None
            else int(channel_fee)
        )
        data = encode_call(
            "withdraw(uint256,bytes,address,bytes32,bytes)",
            ["uint256", "bytes", "address", "bytes32", "bytes"],
            [
                int(amount),
                recipient_bytes,
                normalize_address(refund_address),
                _normalize_bytes32_value(salt),
                _normalize_hex_bytes(signature),
            ],
        )
        # the contract requires msg.value == channelFee (exactly)
        return self._send_call(
            data=data,
            value=resolved_channel_fee,
            private_key=private_key,
            chain_id=chain_id,
            gas_limit=gas_limit,
            max_fee_per_gas=max_fee_per_gas,
            max_priority_fee_per_gas=max_priority_fee_per_gas,
            use_legacy=use_legacy,
            nonce_ms=nonce_ms,
        )


def _validate_uint32(value: int | str) -> int:
    resolved = int(value)
    if resolved < 0 or resolved > 0xFFFFFFFF:
        raise ValueError("dst_chain_id must fit in uint32")
    return resolved


__all__ = [
    "API_VERSION",
    "BITCOIN_MAINNET_CHAIN_ID",
    "BITCOIN_TESTNET_ADDRESS_TYPES",
    "BITCOIN_TESTNET_CHAIN_ID",
    "BITCOIN_TESTNET_FALLBACKS",
    "BRIDGE_IN_EVENT_TOPIC",
    "BRIDGE_TOKEN_MAP",
    "BridgeApi",
    "BridgeFeeQuote",
    "BridgeServiceClient",
    "BtcChannelApi",
    "SOLANA_DEVNET_CHAIN_ID",
    "SOLANA_MAINNET_CHAIN_ID",
    "SOLANA_TO_ETH_BRIDGE_PAYLOAD_LENGTH",
    "add_bridge_fee",
    "apply_bridge_history_subaccount_address",
    "create_bridge_status_check_update",
    "create_sign_bridge_out_request_body",
    "fetch_sign_bridge_out_signature",
    "format_bitcoin_address",
    "format_bridge_fee",
    "format_bridge_total_amount",
    "from_satoshis",
    "generate_bitcoin_deposit_data",
    "generate_bridge_salt",
    "get_bitcoin_address_network",
    "get_bitcoin_address_type",
    "get_bitcoin_balance_satoshis",
    "get_bridge_status_check_dst_hash",
    "get_bridge_status_check_hash",
    "get_bridge_status_check_src_hash",
    "get_bridge_token_id",
    "get_bridge_total_amount",
    "get_solana_bridge_fee_payload_length",
    "get_sign_bridge_out_url",
    "get_withdraw_dst_recipient",
    "is_valid_bitcoin_address",
    "merge_bridge_history_supplement",
    "merge_bridge_history_update",
    "needs_bridge_history_supplement",
    "normalize_bitcoin_deposit_utxos",
    "normalize_bridge_amount",
    "parse_bridge_amount",
    "solana_address_to_bytes32",
    "to_satoshis",
]
