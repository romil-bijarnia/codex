#!/usr/bin/env python3
"""Build an opt-in catalog with targeted work-continuity prompt edits."""

import argparse
import json
import os
from pathlib import Path
import tempfile


TARGETS = ("gpt-6.2-sol", "gpt-6.2-astra")
WAIT = (
    "For optional clarification, give the user a reasonable opportunity to reply - "
    "for example, 30 seconds for a simple multi-choice question and longer for "
    "complex and bundled questions — before proceeding with a stated assumption."
)
PROCEED = (
    "If a reasonable, reversible assumption lets you proceed within scope, proceed "
    "without asking or waiting for optional clarification. State the assumption "
    "if it materially affects the result."
)
COMPLETION = (
    "Before calling work complete, check each deliverable against the user's intended "
    "goal, outcome, deliverable, and format. Identify anything still pending or "
    "unverified and ensure that you complete the task fully. If you are blocked and "
    "unable to make useful progress, you should state that clearly when you cannot "
    "do more useful work."
)
ACCEPTANCE = (
    "For implementation work, derive an observable acceptance check from the current "
    "request. Update the check when the user changes a requirement; do not keep a "
    "superseded condition. Verify the actual workflow the user requested. A passing "
    "prerequisite or substitute workflow does not establish success. If an approach "
    "fails, identify which current requirement it blocks and continue useful authorized "
    "work that can still advance the requested outcome. Call the job blocked only "
    "when no such work remains."
)


MISSING_SKILL = (
    "If the skill is not found and the skill is necessary to do the user's task, "
    "stop the turn and tell the user why."
)
SCOPED_SKILL = (
    "If the skill is not found, say which work depends on it and continue the "
    "independent work."
)


def patch_catalog(source):
    models = source["models"]
    for slug in TARGETS:
        matches = [model for model in models if model.get("slug") == slug]
        if len(matches) != 1:
            raise ValueError(f"{slug}: expected exactly one catalog entry")
        messages = matches[0]["model_messages"]
        template = messages["instructions_template"]
        replacements = (
            (WAIT, PROCEED),
            (COMPLETION, COMPLETION + "\n\n" + ACCEPTANCE),
            (MISSING_SKILL, SCOPED_SKILL),
        )
        for anchor, replacement in replacements:
            if template.count(anchor) != 1 or replacement in template:
                raise ValueError(f"{slug}: expected a unique stock anchor: {anchor[:48]!r}")
            template = template.replace(anchor, replacement, 1)
        messages["instructions_template"] = template
    # The cache envelope contains identity and freshness metadata, not model config.
    return {"models": models}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Unmodified model cache JSON")
    parser.add_argument("--output", type=Path, required=True, help="Separate generated catalog JSON")
    args = parser.parse_args()
    if args.output.resolve() == args.source.resolve():
        parser.error("write to a separate file; do not overwrite the provider cache")
    try:
        catalog = patch_catalog(json.loads(args.source.read_text(encoding="utf-8")))
    except (OSError, KeyError, TypeError, ValueError) as error:
        parser.exit(1, f"{error}\n")

    # NamedTemporaryFile is private and preserves the old output if validation fails.
    temp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=args.output.parent, delete=False
        ) as stream:
            temp = Path(stream.name)
            json.dump(catalog, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temp, args.output)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
