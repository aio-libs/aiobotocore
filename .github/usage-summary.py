"""Parse Claude Code execution file and write usage summary."""

import json
import os
import sys


def main():
    ef = os.environ.get("EXEC_FILE", "")
    if not ef or not os.path.exists(ef):
        print("No execution file found")
        return

    with open(ef) as f:
        data = json.load(f)

    # execution_file is a JSON array of messages.
    # The last entry with type "result" has the summary.
    result = {}
    entries = data if isinstance(data, list) else [data]
    for entry in entries:
        if isinstance(entry, dict) and entry.get("type") == "result":
            result = entry

    usage = result.get("modelUsage", {})
    if not usage:
        print("No usage data found")
        return

    total = result.get("total_cost_usd", 0)
    turns = result.get("num_turns", 0)
    duration_s = result.get("duration_ms", 0) / 1000

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    out = open(summary_path, "a") if summary_path else sys.stdout

    out.write("\n## Usage\n")
    out.write(f"Turns: {turns} | Duration: {duration_s:.0f}s")
    out.write(f" | Total: **${total:.2f}**\n\n")
    out.write("| Model | Input | Output |")
    out.write(" Cache read | Cache create | Cost |\n")
    out.write("|-|-|-|-|-|-|\n")
    for model, m in usage.items():
        name = model.split("-")[1].title()
        out.write(
            f"| {name}"
            f" | {m.get('inputTokens', 0):,}"
            f" | {m.get('outputTokens', 0):,}"
            f" | {m.get('cacheReadInputTokens', 0):,}"
            f" | {m.get('cacheCreationInputTokens', 0):,}"
            f" | ${m.get('costUSD', 0):.2f} |\n"
        )

    if out is not sys.stdout:
        out.close()

    print(f"Turns: {turns} | ${total:.2f}")


if __name__ == "__main__":
    main()
