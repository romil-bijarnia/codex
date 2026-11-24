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
    "Skip optional questions and keep working."
)
COMPLETION = (
    "Before calling work complete, check each deliverable against the user's intended "
    "goal, outcome, deliverable, and format. Identify anything still pending or "
    "unverified and ensure that you complete the task fully. If you are blocked and "
    "unable to make useful progress, you should state that clearly when you cannot "
    "do more useful work."
)
ACCEPTANCE = (
    "Do the work the user asked for within the scope they set. For implementation "
    "work, define the check for success from the current request and update it when a "
    "requirement changes. Verify the requested result through the workflow the user "
    "will actually use. If the requested result already exists, verify it and stop. "
    "When an approach fails, identify which requirement it affects "
    "and carry out the useful work that still advances the requested result. Call the "
    "job blocked only when no such work remains."
)

MISSING_SKILL = (
    "If the skill is not found and the skill is necessary to do the user's task, "
    "stop the turn and tell the user why."
)
SCOPED_SKILL = (
    "If the skill is not found, say which work depends on it and continue the "
    "independent work."
)


CUT_CONTRAST = "IMPORTANT: Never add what you won't do, state what something is not, what will remain unchanged, or how you'll separate or categorize results. NEVER use contrastive framing such as \"X, not Y\", \"These are X, not Y\", \"I'll do X rather than Y\", \"it is about X, not about Y\", and \"X—not Y\". Do not compare to an unsolicited alternative that the user did not directly ask about. Avoid invented compound labels like \"exact-head checks\" and \"editorial-row layouts\", vague qualifiers, and canned transitions. Carefully follow these instructions when writing in documents, slides or other artifacts as well.\n\n"
CUT_CONTRAST_REPLACEMENT = ""

CUT_PUNCTUATION = "In prose, do not use semicolons. Do not use colons to introduce explanations or examples, including after list labels. Avoid replacing colons directly with a period, instead rephrase the thought. \n\n"
CUT_PUNCTUATION_REPLACEMENT = ""

TOOL_PREAMBLE = "If the user's request requires calling tools, start with a message in the `commentary` channel. Prefer sending consistent, frequent communication to the user during your turn. "
TOOL_PREAMBLE_REPLACEMENT = ""

FINAL_REPORT = "In your final answer back to the user, mention any relevant flaws in your work or any decisions you made without explicit guidance from the user, so that the user can verify your approach and work. Disclosure in commentary alone is not enough. You MUST repeat any such disclosures in the final answer."
FINAL_REPORT_REPLACEMENT = "Report the result, how it was verified, and any material limits or decisions."

CONFIRMATION_PREFACE = "The user gets very frustrated when you stop and ask for confirmation or permission, so make sure to explicitly explain why you need the confirmation (for example, a SKILL.md, AGENTS.md, memory, or approval auto-review block) and where it came from."
CONFIRMATION_PREFACE_REPLACEMENT = "When confirmation is required, identify the action and the reason."

DIRECT_SEARCH = "- When you search for text or files, you reach first for `rg` or `rg --files`; they are much faster than alternatives like `grep`. If `rg` is unavailable, you use the next best tool without fuss."
DIRECT_SEARCH_REPLACEMENT = "- When searching live files, prefer `rg` or `rg --files`. Use the next best tool if unavailable."

RETRY_EVIDENCE = "If an attempt fails, diagnose the failure, try a permitted recovery, and finish independent work."
RETRY_EVIDENCE_REPLACEMENT = "If an attempt fails, diagnose it, try a permitted recovery, and finish independent work. Before repeating an action, identify what new evidence it could produce and why the result might differ. If neither is clear, change the approach or state the unresolved limitation."

ACTION_REPEATS = {
    "gpt-6.2-sol": "Bias towards action, and persist towards completing the user's task before stopping to end a turn. If a task requires sustained work, do all the necessary work until the intended outcome is completely fulfilled.\n\n",
    "gpt-6.2-astra": "Bias towards action, and persist until you've completed the task the user asked for. Avoid stopping to end the turn while the task is incomplete or if there is an authorized next step you can take, and instead take the next steps within task scope to progress the user's request. Make sure that you have accounted for every part of the user's request, and persist until you've delivered a result that fully satisfies the user's goal. If a task requires sustained work, do all the necessary work until the intended outcome is completely fulfilled.\n\n"
}


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
            (CUT_CONTRAST, CUT_CONTRAST_REPLACEMENT),
            (CUT_PUNCTUATION, CUT_PUNCTUATION_REPLACEMENT),
            (TOOL_PREAMBLE, TOOL_PREAMBLE_REPLACEMENT),
            (FINAL_REPORT, FINAL_REPORT_REPLACEMENT),
            (CONFIRMATION_PREFACE, CONFIRMATION_PREFACE_REPLACEMENT),
            (DIRECT_SEARCH, DIRECT_SEARCH_REPLACEMENT),
            (RETRY_EVIDENCE, RETRY_EVIDENCE_REPLACEMENT),
            (ACTION_REPEATS[slug], ""),
        )
        for anchor, replacement in replacements:
            if template.count(anchor) != 1 or (replacement and replacement in template):
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
