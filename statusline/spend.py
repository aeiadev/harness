"""Shared spend rendering and optional, explicitly configured Codex estimates."""

import json
import os

import storage


def reported_spend(data):
    cost = data.get("cost")
    return storage.quantity(cost.get("total_cost_usd")) if isinstance(cost, dict) else None


def segment(spend, color=False):
    text = f"${spend:.2f}" if spend is not None else "$--"
    threshold = storage.quantity(os.environ.get("HARNESS_SPEND_WARN"))
    if color and spend is not None and threshold is not None and spend > threshold:
        text = "\033[33m" + text + "\033[0m"
    return text


def usage_counts(usage):
    if not isinstance(usage, dict):
        return None
    values = tuple(storage.quantity(usage.get(key)) for key in (
        "input_tokens", "cached_input_tokens", "output_tokens"))
    if any(value is None for value in values) or values[1] > values[0]:
        return None
    return values


def rate_cost(counts, model, rates):
    if not isinstance(model, str):
        return None
    rate = rates.get(model)
    if not isinstance(rate, dict):
        return None
    prices = [storage.quantity(rate.get(key)) for key in ("input", "cached_input", "output")]
    if any(price is None for price in prices):
        return None
    incoming, cached, outgoing = counts
    return ((incoming - cached) * prices[0] + cached * prices[1]
            + outgoing * prices[2]) / 1000000


def codex_spend(data):
    """Return provided cost, or an estimate only with complete usage and rates."""
    reported = reported_spend(data)
    if reported is not None:
        return reported, False
    try:
        rates = json.loads(os.environ.get("HARNESS_CODEX_RATES", "{}"))
    except (ValueError, RecursionError):
        return None, False
    if not isinstance(rates, dict) or not rates:
        return None, False
    if data.get("type") == "turn.completed":
        counts = usage_counts(data.get("usage"))
        price = rate_cost(counts, data.get("model"), rates) if counts else None
        return price, price is not None
    entries, complete = storage.log_records(data.get("transcript_path"))
    if not complete:
        return None, False
    total = 0.0
    found = False
    previous = (0, 0, 0)
    model = None
    # Rollouts report cumulative usage. Repeated token_count notifications must
    # not be added twice; price only their deltas using the active turn's model.
    rollout = any(entry.get("type") == "event_msg"
                  and isinstance(entry.get("payload"), dict)
                  and entry["payload"].get("type") == "token_count" for entry in entries)
    for entry in entries:
        payload = entry.get("payload")
        if entry.get("type") == "turn_context" and isinstance(payload, dict):
            model = payload.get("model")
        usage = None
        if rollout:
            if (entry.get("type") == "event_msg" and isinstance(payload, dict)
                    and payload.get("type") == "token_count"):
                info = payload.get("info")
                if isinstance(info, dict):
                    usage = info.get("total_token_usage")
        elif entry.get("type") == "turn.completed":
            usage = entry.get("usage")
            model = entry.get("model", data.get("model"))
        if usage is None:
            continue
        counts = usage_counts(usage)
        if counts is None:
            return None, False
        if rollout:
            delta = tuple(value - old for value, old in zip(counts, previous))
            if any(value < 0 for value in delta) or delta[1] > delta[0]:
                return None, False
            previous = counts
        else:
            delta = counts
        price = rate_cost(delta, model, rates)
        if price is None:
            return None, False
        total += price
        found = True
    result = storage.quantity(total) if found else None
    return result, result is not None


def codex_tokens(data):
    """Return complete input plus output usage without guessing from partial history."""
    if data.get("type") == "turn.completed":
        counts = usage_counts(data.get("usage"))
        return counts[0] + counts[2] if counts else None
    entries, complete = storage.log_records(data.get("transcript_path"))
    if not complete:
        return None
    rollout = any(entry.get("type") == "event_msg"
                  and storage.object_value(entry.get("payload")).get("type") == "token_count"
                  for entry in entries)
    total = 0
    found = False
    for entry in entries:
        payload = storage.object_value(entry.get("payload"))
        if rollout:
            if entry.get("type") != "event_msg" or payload.get("type") != "token_count":
                continue
            usage = storage.object_value(payload.get("info")).get("total_token_usage")
        elif entry.get("type") == "turn.completed":
            usage = entry.get("usage")
        else:
            continue
        counts = usage_counts(usage)
        if counts is None:
            return None
        amount = counts[0] + counts[2]
        if rollout and found and amount < total:
            return None
        total = amount if rollout else total + amount
        found = True
    return total if found else None


def token_segment(count):
    return f"{count / 1000:.1f}k tok" if count is not None else ""
