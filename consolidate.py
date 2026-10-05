"""Fold the existing Bulk/<something> labels into one Bulk label."""

import sys

from email_tools import consolidate_bulk_labels

preview = consolidate_bulk_labels(dry_run=True)

if not preview["labels"]:
    print("No sub-labels to fold in - nothing to do.")
    sys.exit()

print(f"{len(preview['labels'])} labels to fold into Bulk:")
for name in preview["labels"]:
    print(f"  {name}")
print()
print("The mail stays where it is and keeps the Bulk label.")
print("Only the sub-labels are removed.")

if input("go ahead? [y/N] ").strip().lower() != "y":
    print("Left alone.")
    sys.exit()

result = consolidate_bulk_labels(dry_run=False)
print(f"\n{result['moved']} messages now under Bulk")
print(f"{len(result['labels'])} sub-labels removed")
