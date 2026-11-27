"""Adapt perp calls to the runtime metadata used for SCALE encoding."""

from __future__ import annotations

from typing import Any

from ._errors import ValidationError


def adapt_perp_call(
    substrate: Any, module: str, function: str, params: dict,
) -> dict:
    is_perp = module == "PerpMarket" and function in {
        "place_order", "set_profit_and_loss_point", "update_tp_sl",
    }
    if not is_perp and (module, function) != ("Subaccount", "modify_orders"):
        return params

    pallet = substrate.metadata.get_metadata_pallet("PerpMarket")
    calls = {call.name: call for call in pallet.calls} if pallet else {}
    if function == "set_profit_and_loss_point" and function not in calls:
        raise ValidationError(
            "This runtime does not support set_profit_and_loss_point. Use update_tp_sl "
            "to create or cancel position TP/SL conditions."
        )
    if function == "update_tp_sl" and function not in calls:
        raise ValidationError("This runtime does not support update_tp_sl.")

    if function == "place_order" and _uses_attached_configs(substrate, calls):
        return {**params, "params": _encode_attached_configs(params["params"])}
    if function == "modify_orders":
        total_quantity = params.get("_perp_total_quantity")
        params = {key: value for key, value in params.items() if key != "_perp_total_quantity"}
        if _uses_attached_configs(substrate, calls):
            return _adapt_modify_orders(substrate, params, total_quantity)
    return params


def _uses_attached_configs(substrate: Any, calls: dict) -> bool:
    if "place_order" not in calls:
        return False
    params_type = calls["place_order"].value["fields"][0]["type"]
    fields = substrate.metadata.portable_registry.value["types"][params_type][
        "type"
    ]["def"]["composite"]["fields"]
    return any(
        field["name"] == "take_profit" and field["typeName"] == "Option<TpSlConfig>"
        for field in fields
    )


def _encode_attached_configs(params: dict) -> dict:
    encoded = dict(params)
    for field, condition in (("take_profit", "TakeProfit"), ("stop_loss", "StopLoss")):
        price = params.get(field)
        if isinstance(price, int):
            encoded[field] = {
                "condition": condition,
                "source": "Mark",
                "trigger_price": price,
                "quantity": {"Partial": params["size"]},
                "execution": {"Market": None},
            }
    return encoded


def _adapt_modify_orders(substrate: Any, params: dict, total_quantity: int | None) -> dict:
    ops = params["ops"]
    # The SDK's atomic modify uses exactly one Cancel followed by one Place.
    if len(ops) != 2:
        return params
    cancel = ops[0].get("Cancel", {}).get("Perp")
    place = ops[1].get("Place", {}).get("Perp")
    if cancel is None or place is None:
        return params

    record = substrate.query(
        "PerpMarket", "ConditionalOrders", [cancel["subaccount"], cancel["order_id"]],
        block_hash=substrate.block_hash,
    ).value
    if record is not None:
        # Conditional replacements take total size. Parent orders subtract the
        # current order's fills; reduce-only children subtract all chain fills.
        if record["kind"] == "Parent" and total_quantity is not None:
            size = total_quantity
        else:
            fills = (
                "chain_fills" if record["kind"] != "Parent" and place["reduce_only"] else "fills"
            )
            size = place["size"] + record[fills]["size"]
        place = {**place, "size": size}

    return {**params, "ops": [ops[0], {"Place": {"Perp": _encode_attached_configs(place)}}]}
