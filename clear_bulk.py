"""Work through the bulk categories, approving one at a time.

Reads the cached breakdown, shows each bulk category with its counts and a
real subject, and asks. Approving files that batch under its own label and
takes it out of the inbox.

Nothing is deleted here, ever. The label is the point: Gmail groups the
batch under Bulk/, so deleting it later is a select-all in an interface
where you can see exactly what you are removing.
"""

import json
import os
import sys

from email_tools import (BULK_PARENT, archive_batch, find_uids_for_patterns,
                         safe_label_name)

CACHE = "inbox_categories.json"
DONE = "archived_batches.json"


def load(path):
    if not os.path.exists(path):
        return None
    with open(path) as handle:
        return json.load(handle)


def record(entry, path=DONE):
    log = load(path) or []
    log.append(entry)
    with open(path, "w") as handle:
        json.dump(log, handle, indent=2)


def run(min_count=1, auto_yes=False):
    data = load(CACHE)
    if not data:
        print(f"No {CACHE} - run: python categorise_all.py build")
        return

    already = {
        (e["sender"], e["category"]) for e in (load(DONE) or [])
        if e.get("applied")
    }

    batches = []
    for sender in data["senders"]:
        for category in sender["categories"]:
            if category["needs_attention"] or category["count"] < min_count:
                continue
            if (sender["address"], category["name"]) in already:
                continue
            batches.append((sender, category))

    if not batches:
        print("Nothing left to clear.")
        return

    batches.sort(key=lambda pair: pair[1]["count"], reverse=True)
    pending = sum(c["count"] for _, c in batches)
    print(f"{len(batches)} categories, {pending} messages\n")

    moved = 0
    for index, (sender, category) in enumerate(batches, 1):
        name = sender["name"] or sender["address"]
        label = safe_label_name(category["name"], name)

        print(f"[{index}/{len(batches)}] {name}")
        print(f"    {category['name']} - {category['count']} messages")
        for pattern in category["patterns"][:3]:
            print(f"      {pattern['count']:>4}  {pattern['example'][:64]}")
        if len(category["patterns"]) > 3:
            print(f"      ... and {len(category['patterns']) - 3} more patterns")
        print(f"    -> label: {label}")

        if auto_yes:
            answer = "y"
        else:
            try:
                answer = input("    archive this? [y/N/q] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nStopped.")
                break

        if answer == "q":
            print("Stopped.")
            break
        if answer != "y":
            print("    skipped\n")
            continue

        # Re-find the messages now rather than trusting the cached scan:
        # mail may have arrived since, and the label should cover it.
        uids = find_uids_for_patterns(sender["address"],
                                      [p["example"] for p in category["patterns"]])
        if not uids:
            print("    nothing matched - skipped\n")
            continue

        if len(uids) != category["count"]:
            print(f"    note: {len(uids)} match now, preview said "
                  f"{category['count']}")

        result = archive_batch(uids, label, dry_run=False)
        if result.get("applied"):
            moved += result["count"]
            print(f"    archived {result['count']} under {label}\n")
            record({
                "sender": sender["address"],
                "name": sender["name"],
                "category": category["name"],
                "label": label,
                "count": result["count"],
                "applied": True,
            })
        else:
            print(f"    failed: {result.get('error', 'unknown')}\n")

    print(f"Moved {moved} messages out of the inbox.")
    if moved:
        print(f"They are under {BULK_PARENT}/ in Gmail, still searchable.")
        print("To delete a batch: open its label in Gmail, select all, delete.")


if __name__ == "__main__":
    # --min N skips the small ones; --yes approves everything without asking.
    min_count = 1
    if "--min" in sys.argv:
        min_count = int(sys.argv[sys.argv.index("--min") + 1])
    run(min_count=min_count, auto_yes="--yes" in sys.argv)
