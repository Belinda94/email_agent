"""Categorise the whole inbox, sender by sender, and cache the result.

One IMAP scan groups every message by sender and subject pattern. Senders
above a threshold then get one small API call each to name their categories;
everything below the threshold is reported as a tail rather than paid for.

The result is written to inbox_categories.json. That cache is not just a
speed trick: the gate has to act on exactly the categories you were shown,
and recomputing them would let the grouping drift between preview and
approval.
"""

import json
import os
import sys
from datetime import datetime

from categorise import DEFAULT_CONTEXT, categorise_patterns
from email_tools import root_domain, scan_inbox

CACHE = "inbox_categories.json"

# Below this, a sender is not worth an API call - a handful of one-off
# messages has no pattern to find.
MIN_MESSAGES = 10


def normalise_name(name):
    """Loose display-name match, for deciding if two addresses are one sender."""
    return "".join(ch for ch in name.lower() if ch.isalnum())


def merge_siblings(senders):
    """Join addresses that are the same sender wearing two subdomains.

    The rule is same root domain AND same display name. That merges
    rides-promotions.bolt.eu with rides-marketing.bolt.eu, and the two
    Remote4Africa addresses, while leaving Google Play, Google Careers and
    Google Accounts apart - those share a domain but are genuinely
    different mail.
    """
    groups = {}
    for address, sender in senders.items():
        key = (root_domain(address), normalise_name(sender.get("name", "")))
        groups.setdefault(key, []).append((address, sender))

    merged = {}
    for (domain, _), members in groups.items():
        if len(members) == 1:
            address, sender = members[0]
            merged[address] = sender
            continue

        members.sort(key=lambda kv: kv[1]["count"], reverse=True)
        primary_address, primary = members[0]

        combined = {}
        for _, sender in members:
            for pattern in sender["patterns"]:
                key = pattern["example"]
                row = combined.setdefault(
                    key, {"count": 0, "example": key, "also_seen": []}
                )
                row["count"] += pattern["count"]

        merged[primary_address] = {
            "name": primary["name"],
            "domain": domain,
            "count": sum(s["count"] for _, s in members),
            "patterns": sorted(combined.values(),
                               key=lambda p: p["count"], reverse=True),
            "merged_addresses": [a for a, _ in members[1:]],
        }

    return merged


def build(limit=2000, min_messages=MIN_MESSAGES, cache_path=CACHE,
          context=None):
    print("Scanning the mailbox...")
    scan = scan_inbox(limit=limit)

    senders = merge_siblings(scan["senders"])
    if not senders:
        print("Nothing found.")
        return None

    ranked = sorted(senders.items(), key=lambda kv: kv[1]["count"], reverse=True)
    big = [(a, s) for a, s in ranked if s["count"] >= min_messages]
    tail = [(a, s) for a, s in ranked if s["count"] < min_messages]

    scanned = sum(s["count"] for _, s in ranked)
    print(f"{scan['total']} messages"
          + (f", scanned the newest {scanned}" if scan["truncated"] else "")
          + f" from {len(senders)} senders")
    print(f"{len(big)} senders have {min_messages}+ messages; "
          f"{len(tail)} are one-offs totalling "
          f"{sum(s['count'] for _, s in tail)}")
    print()

    result = {
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "scanned": scanned,
        "total": scan["total"],
        "truncated": scan["truncated"],
        "min_messages": min_messages,
        "senders": [],
        "tail": [
            {"address": a, "name": s["name"], "count": s["count"]}
            for a, s in tail
        ],
    }

    for index, (address, sender) in enumerate(big, 1):
        name = sender["name"] or address
        print(f"[{index}/{len(big)}] {name} ({sender['count']}) ... ", end="", flush=True)
        try:
            categories = categorise_patterns(
                sender["patterns"], context=context or DEFAULT_CONTEXT
            )
            print(f"{len(categories)} categories")
        except Exception as exc:
            # One sender failing should not lose the whole run.
            print(f"failed ({exc})")
            categories = []

        result["senders"].append({
            "address": address,
            "name": sender["name"],
            "domain": sender["domain"],
            "count": sender["count"],
            "merged_addresses": sender.get("merged_addresses", []),
            "categories": categories,
        })

    with open(cache_path, "w") as handle:
        json.dump(result, handle, indent=2)

    print()
    print(f"Written to {cache_path}")
    return result


def show(cache_path=CACHE, attention=None):
    """Print the cached breakdown. attention=False shows only bulk mail."""
    if not os.path.exists(cache_path):
        print(f"No {cache_path} yet - run with 'build' first.")
        return

    with open(cache_path) as handle:
        data = json.load(handle)

    print(f"Built {data['built_at']} over {data['scanned']} messages")
    print()

    for sender in data["senders"]:
        rows = [
            c for c in sender["categories"]
            if attention is None or c["needs_attention"] == attention
        ]
        if not rows:
            continue
        print(f"{sender['name'] or sender['address']}  <{sender['address']}>")
        for extra in sender.get("merged_addresses", []):
            print(f"     + {extra}")
        for category in rows:
            flag = "keep?" if category["needs_attention"] else "bulk "
            print(f"   [{flag}] {category['count']:>4}  {category['name']}")
        print()

    if data["tail"] and attention is not False:
        total = sum(t["count"] for t in data["tail"])
        print(f"Plus {len(data['tail'])} occasional senders, {total} messages, "
              "not categorised")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "show"

    if command == "build":
        build()
    elif command == "bulk":
        show(attention=False)
    else:
        show()
