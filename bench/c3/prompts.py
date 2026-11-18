"""Expand the five C3 arm prompts for each fixed task, verbatim from the frozen spec.

A prompt is: shared instruction, task build request, reference direction, exactly one arm block,
and the runtime/tool placeholder. The placeholder is filled at manifest time. Nothing here runs a
model. The frozen text lives in prompt_text.json; tests check it against the spec.

Usage: python bench/c3/prompts.py OUT_DIR
"""

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASKS_PATH = HERE / "tasks.json"
TEXT_PATH = HERE / "prompt_text.json"
RUNTIME_PLACEHOLDER = "[[RUNTIME_AND_TOOL_INSTRUCTIONS: filled at manifest time]]"
_TEXT = json.loads(TEXT_PATH.read_text(encoding="utf-8"))
SHARED_INSTRUCTION = _TEXT["shared_instruction"]
ARM_BLOCKS = _TEXT["arms"]


def load_tasks():
    return json.loads(TASKS_PATH.read_text(encoding="utf-8"))["tasks"]


def expand(task, arm_id):
    # ponytail: the reference line is an assembler addition, so each arm block can say "the named
    # CC0 starter" and the prompt still names it. The spec gives no literal wording for it.
    parts = [
        SHARED_INSTRUCTION,
        task["build_request"],
        f"Reference direction: {task['reference']}",
        ARM_BLOCKS[arm_id],
        RUNTIME_PLACEHOLDER,
    ]
    return "\n\n".join(parts)


def write_all(out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for task in load_tasks():
        for arm_id in ARM_BLOCKS:
            path = out / f"{task['id']}-{arm_id}.txt"
            path.write_text(expand(task, arm_id) + "\n", encoding="utf-8")
            written.append(path)
    return written


def main(argv=None):
    parser = argparse.ArgumentParser(description="Write the expanded C3 prompts.")
    parser.add_argument("out_dir")
    args = parser.parse_args(argv)
    print(f"wrote {len(write_all(args.out_dir))} prompts to {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
