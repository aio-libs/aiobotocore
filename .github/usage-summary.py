"""Parse Claude Code execution file and write usage summary."""

import json
import os
import sys


def main():
    ef = os.environ.get("EXEC_FILE", "")
    if not ef or not os.path.exists(ef):
        print("No execution file found")
        return

    data = json.load(open(ef))
    usage = data.get("modelUsage", {})
    if not usage:
        print("No usage data")
        return

    total = sum(m.get("costUSD", 0) for m in usage.values())

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        # Print to stdout if no summary file
        out = sys.stdout
    else:
        out = open(summary_path, "a")

    out.write("\n## Usage\n")
    out.write("| Model | Input | Output |")
    out.write(" Cache read | Cost |\n")
    out.write("|-|-|-|-|-|\n")
    for model, m in usage.items():
        name = model.split("-")[1].title()
        out.write(
            f"| {name} "
            f"| {m.get('inputTokens', 0):,} "
            f"| {m.get('outputTokens', 0):,} "
            f"| {m.get('cacheReadInputTokens', 0):,} "
            f"| ${m.get('costUSD', 0):.2f} |\n"
        )
    out.write(f"| **Total** | | | | **${total:.2f}** |\n")

    if out is not sys.stdout:
        out.close()

    print(f"Total cost: ${total:.2f}")


if __name__ == "__main__":
    main()
