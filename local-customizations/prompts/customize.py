"""Apply the personal communication rules without changing other prompt sections."""
from pathlib import Path


def customize_prompt(prompt: str, rules: Path) -> str:
    heading = '## Writing style\n\n'
    following = '\n## Technical communication'
    if prompt.count(heading) != 1 or prompt.count(following) != 1:
        raise ValueError('The writing-style section changed; review the prompt before applying these rules.')
    start = prompt.index(heading) + len(heading)
    end = prompt.index(following, start)
    prompt = prompt[:start] + (rules / 'writing-style.md').read_text().strip() + '\n' + prompt[end:]

    replacements = [
        (
            "If the user's intent or task scope is unclear, progress towards the user's goal with the information available and then ask the user for clarification while continuing independent work.",
            (rules / 'investigation.md').read_text().strip(),
        ),
        (
            "Ask clarifying questions early unless the user's answers can potentially be inferred from available context, and continue useful work that does not depend on the answer while waiting.",
            'Investigate before asking clarifying questions. Ask only for necessary information you cannot obtain or infer yourself, and continue useful work that does not depend on the answer while waiting.',
        ),
    ]
    for original, replacement in replacements:
        if replacement in prompt:
            continue
        if prompt.count(original) != 1:
            raise ValueError('The clarification guidance changed; review the prompt before applying these rules.')
        prompt = prompt.replace(original, replacement, 1)
    return prompt
