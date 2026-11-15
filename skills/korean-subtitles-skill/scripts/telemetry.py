"""Optional read-only usage analysis of Codex-native logs; never required for other hosts."""

from collections import Counter
from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import subprocess


class Error(ValueError):
    pass


def instant(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset().total_seconds():
        raise Error("Use UTC timestamps")
    return result


@contextmanager
def lines(path):
    if path.suffix != ".zst":
        with path.open(encoding="utf-8") as stream:
            yield stream
        return
    process = subprocess.Popen(
        ["zstd", "-dc", "--", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        yield process.stdout
        process.stdout.close()
        detail = process.stderr.read()
        if process.wait():
            raise Error("Cannot decompress session log: " + detail.strip())
    finally:
        process.stdout.close()
        process.stderr.close()
        if process.poll() is None:
            process.kill()
        process.wait()


def union_ms(intervals):
    merged = []
    for start, end in sorted(intervals):
        if end < start:
            raise Error("Negative observed interval")
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return sum(end - start for start, end in merged)


def usage_logs(paths, start, end, root_turn_ids=()):
    if not start or not end:
        raise Error("Telemetry needs an exact UTC start/end interval")
    lo, hi = instant(start), instant(end)
    if lo > hi:
        raise Error("Use ordered UTC telemetry timestamps")
    if not paths:
        raise Error("Provide exact coordinator and worker session logs")
    roots = set(root_turn_ids or ())
    seen_roots = set()
    global_responses, rows, intervals, notices = {}, [], [], []
    totals = Counter()
    for name in dict.fromkeys(str(Path(p).resolve()) for p in paths):
        path = Path(name)
        owner = None
        native = {}
        counters = []
        items = []
        turns = {}
        with lines(path) as stream:
            for line in stream:
                # Discard large image/message payloads before JSON decoding.
                if not any(
                    marker in line
                    for marker in (
                        '"session_meta"',
                        '"token_usage_record"',
                        '"token_count"',
                        '"item_completed"',
                    )
                ):
                    continue
                event = json.loads(line)
                payload = event.get("payload", {})
                typ = event.get("type")
                if typ == "session_meta" and owner is None:
                    owner = payload["id"]
                elif typ == "token_usage_record":
                    if owner is not None and payload.get("thread_id") != owner:
                        continue
                    key = (payload["thread_id"], payload["response_id"])
                    if key in native and native[key]["usage"] != payload["usage"]:
                        raise Error("Conflicting response usage")
                    native[key] = {**payload, "timestamp": event["timestamp"]}
                    turns[payload["turn_id"]] = payload.get("root_turn_id")
                elif typ == "event_msg" and payload.get("type") == "token_count":
                    usage = (payload.get("info") or {}).get("total_token_usage")
                    if usage:
                        counters.append(
                            (
                                instant(event["timestamp"]),
                                usage,
                                payload.get("rate_limits", {}),
                            )
                        )
                elif typ == "event_msg" and payload.get("type") == "item_completed":
                    if payload.get("thread_id") == owner:
                        items.append(
                            (
                                event["timestamp"],
                                {
                                    k: payload.get(k)
                                    for k in (
                                        "turn_id",
                                        "started_at_ms",
                                        "completed_at_ms",
                                    )
                                }
                                | {
                                    "item": {
                                        "type": payload.get("item", {}).get("type")
                                    }
                                },
                            )
                        )
        usage = Counter()
        complete = True
        count = 0
        if native:
            observed = Counter()
            last = None
            for key, rec in native.items():
                observed.update(rec["usage"])
                last = rec.get("thread_token_usage")
                t = instant(rec["timestamp"])
                if not lo <= t <= hi or (
                    roots and rec.get("root_turn_id") not in roots
                ):
                    continue
                seen_roots.add(rec.get("root_turn_id"))
                if key in global_responses:
                    if global_responses[key] != rec["usage"]:
                        raise Error("Conflicting duplicated session response")
                    continue
                global_responses[key] = rec["usage"]
                usage.update(rec["usage"])
                count += 1
            complete = last is not None and dict(observed) == last
            if not complete:
                notices.append(
                    {
                        "session": name,
                        "reason": "Native usage does not reconcile with thread cumulative usage",
                    }
                )
            for timestamp, item in items:
                if lo <= instant(timestamp) <= hi and (
                    not roots or turns.get(item.get("turn_id")) in roots
                ):
                    if item.get("item", {}).get("type") == "CommandExecution":
                        a, b = item.get("started_at_ms"), item.get("completed_at_ms")
                        if a is not None and b is not None:
                            intervals.append(
                                (
                                    max(a, int(lo.timestamp() * 1000)),
                                    min(b, int(hi.timestamp() * 1000)),
                                )
                            )
            method = "response_id"
        else:
            method = "cumulative_delta"
            if roots:
                complete = False
                notices.append(
                    {
                        "session": name,
                        "reason": "Root-turn attribution unavailable in counter-only log",
                    }
                )
            else:
                before, last, previous = {}, {}, None
                for timestamp, current, _ in counters:
                    if timestamp > hi:
                        continue
                    if (
                        previous
                        and timestamp >= lo
                        and any(current.get(k, 0) < v for k, v in previous.items())
                    ):
                        raise Error(
                            "Telemetry counter reset; do not report a false delta"
                        )
                    previous = current
                    if timestamp < lo:
                        before = current
                    else:
                        last = current
                if last:
                    usage.update({k: v - before.get(k, 0) for k, v in last.items()})
        if usage:
            if usage.get("cached_input_tokens", 0) > usage.get(
                "input_tokens", 0
            ) or any(v < 0 for v in usage.values()):
                raise Error("Invalid token counters")
            usage["uncached_input_tokens"] = usage.get("input_tokens", 0) - usage.get(
                "cached_input_tokens", 0
            )
            totals.update(usage)
        within = [limits for timestamp, _, limits in counters if lo <= timestamp <= hi]
        rows.append(
            {
                "session": name,
                "thread_id": owner,
                "method": method,
                "responses": count,
                "complete": complete and (bool(native) or bool(usage)),
                "usage": dict(usage) if usage else "not_measured",
                "first_rate_limit": within[0] if within else None,
                "last_rate_limit": within[-1] if within else None,
            }
        )
    missing_roots = roots - seen_roots
    if missing_roots:
        notices.append(
            {
                "reason": "Requested root turns have no attributed responses in the interval",
                "root_turn_ids": sorted(missing_roots),
            }
        )
    complete = (
        bool(totals) and not missing_roots and all(row["complete"] for row in rows)
    )
    return {
        "usage": dict(totals) if totals and complete else "not_measured",
        "sessions": rows,
        "complete": complete,
        "notices": notices,
        "root_turn_ids": sorted(roots),
        "command_interval_union_ms": union_ms(intervals),
        "limits": "Explicit supplied logs only; descendant completeness must be verified by the caller. "
        "Observed shell intervals are not pure CPU time or all tool time. "
        "Input includes cached input; output includes reasoning. Not billing or account quota.",
    }
