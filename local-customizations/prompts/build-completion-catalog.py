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


REFUSAL_SCOPE = (
    "Do not let an earlier refusal or blocker (e.g. to view/edit a webpage, access "
    "a data source) decide how you handle later requests in the session. Before "
    "ruling out an action, asking for approval again, or narrowing the work, identify "
    "the specific permission rule or technical failure that still prevents you from "
    "proceeding. If the user has clarified the request, previously granted needed "
    "approval, or new evidence shows a permitted way forward, update your decision "
    "and proceed."
)
SCOPED_BLOCKER = (
    "Before declining an action or narrowing the work, identify the specific action "
    "affected and whether the reason is a binding policy or authorization boundary, "
    "an observed technical failure, or a missing fact. Do not present uncertainty as "
    "a denial or a technical failure as a policy decision. Apply the limit only to "
    "what it actually prevents and continue independent authorized work. Reassess a "
    "revised request on its current facts, without retrying a denied action through "
    "another route when the denial still applies."
)
MISSING_SKILL = (
    "If the skill is not found and the skill is necessary to do the user's task, "
    "stop the turn and tell the user why."
)
SCOPED_SKILL = (
    "If the skill is not found, continue independent work that does not require it "
    "and that respects the user's requested method. If the skill is required for "
    "remaining work, name the part it blocks. Stop only when no useful authorized "
    "work can proceed."
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
            (REFUSAL_SCOPE, SCOPED_BLOCKER),
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
