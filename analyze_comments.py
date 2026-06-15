"""
analyze_comments.py
-------------------
Reads a comments JSON file (output of youtube_comments.py or
youtube_search_comments.py), sends every comment through Claude for
structured analysis, and writes one enriched JSON file.

Each comment gets these extra fields added:
  - player_name  : athlete/player mentioned, or null
  - country      : their country, or null
  - sentiment    : "Positive" | "Negative" | "Neutral"
  - theme        : 2-4 word topic summary

Usage
-----
  python analyze_comments.py IyZ1WIua_1s_comments.json
  python analyze_comments.py search_dump.json -o analyzed.json
  python analyze_comments.py comments.json --model claude-haiku-4-5
  python analyze_comments.py comments.json --delay 0.5
"""

import json
import time
import argparse
from pathlib import Path
from typing import Any
import requests

CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"

# ---------------------------------------------------------------------------
# Model catalogue  (api_id, display_label, input $/M, output $/M)
# ---------------------------------------------------------------------------
MODELS: list[dict] = [
    {
        "id":       "claude-haiku-4-5",
        "label":    "Haiku 4.5",
        "input":    1.00,
        "output":   5.00,
        "note":     "Cheapest — fast, great for classification & extraction",
    },
    {
        "id":       "claude-sonnet-4-6",
        "label":    "Sonnet 4.6",
        "input":    3.00,
        "output":   15.00,
        "note":     "Balanced — best price-to-quality for most tasks",
    },
    {
        "id":       "claude-opus-4-6",
        "label":    "Opus 4.6",
        "input":    5.00,
        "output":   25.00,
        "note":     "Premium — strongest reasoning, 1M context",
    },
    {
        "id":       "claude-opus-4-7",
        "label":    "Opus 4.7",
        "input":    5.00,
        "output":   25.00,
        "note":     "Latest Opus — new tokenizer, best long-context",
    },
    {
        "id":       "claude-opus-4-8",
        "label":    "Opus 4.8",
        "input":    5.00,
        "output":   25.00,
        "note":     "Newest flagship — adaptive thinking, best coding",
    },
]

# Rough token counts for fixed parts of every API call
SYSTEM_TOKENS   = 80   # system prompt
PROMPT_OVERHEAD = 60   # analysis prompt template (excluding comment text)
AVG_OUTPUT_TOKENS = 40 # typical JSON response

SYSTEM_PROMPT = """You are a YouTube comment analyst.
For every comment you receive, extract the requested fields and return ONLY a valid JSON object — no explanation, no markdown, no code fences.

Always return exactly these keys:
{
  "player_name": "<string or null>",
  "country": "<string or null>",
  "sentiment": "<Positive | Negative | Neutral>",
  "theme": "<2-4 word summary>"
}"""

ANALYSIS_PROMPT = """Analyze the following YouTube comment and extract the requested information.

* player_name: The name of the athlete/player mentioned, or null if none
* country: The player's country, or null if none
* sentiment: "Positive", "Negative", or "Neutral"
* theme: A 2-4 word summary of the main topic or comment focus

Comment: '{comment}'"""


# ---------------------------------------------------------------------------
# Token / cost estimation
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """1 token ≈ 4 characters — holds reasonably for Latin + CJK mix."""
    return max(1, len(text) // 4)


def collect_all_texts(data: dict) -> list[str]:
    """Return every comment + reply text from either JSON shape."""
    texts: list[str] = []
    if "videos" in data:
        for video in data["videos"]:
            for c in video.get("comments", []):
                texts.append(c.get("text", ""))
                for r in c.get("replies", []):
                    texts.append(r.get("text", ""))
    elif "comments" in data:
        for c in data["comments"]:
            texts.append(c.get("text", ""))
            for r in c.get("replies", []):
                texts.append(r.get("text", ""))
    return texts


def calc_cost(texts: list[str], model: dict) -> tuple[int, int, float]:
    """Return (total_input_tokens, total_output_tokens, estimated_cost_usd)."""
    n = len(texts)
    comment_tokens = sum(estimate_tokens(t) for t in texts)
    total_input  = n * (SYSTEM_TOKENS + PROMPT_OVERHEAD) + comment_tokens
    total_output = n * AVG_OUTPUT_TOKENS
    cost = (
        (total_input  / 1_000_000) * model["input"] +
        (total_output / 1_000_000) * model["output"]
    )
    return total_input, total_output, cost


def print_model_menu(texts: list[str], delay: float) -> dict:
    """
    Print a cost comparison table for all models and prompt the user
    to choose one interactively. Returns the chosen model dict.
    """
    n = len(texts)
    col_w = 14

    print()
    print("─" * 78)
    print("  MODEL SELECTION & COST ESTIMATE")
    print("─" * 78)
    print(f"  {'#':<3} {'Model':<14} {'Input $/M':>10} {'Output $/M':>11} "
          f"{'Est. Input tok':>15} {'Est. Cost':>12}  Note")
    print("─" * 78)

    for idx, model in enumerate(MODELS, start=1):
        total_input, total_output, cost = calc_cost(texts, model)
        print(
            f"  {idx:<3} {model['label']:<14} "
            f"${model['input']:>8.2f}  "
            f"${model['output']:>9.2f}  "
            f"{total_input:>15,}  "
            f"${cost:>10.4f}  "
            f"{model['note']}"
        )

    print("─" * 78)
    print(f"  Comments to analyze : {n:,}")
    print(f"  Delay between calls : {delay}s")
    print(f"  Estimated time      : ~{n * delay / 60:.1f} min")
    print("  Note: costs are ±20% estimates. Batch API = 50% off; prompt caching = 90% off cached input.")
    print("─" * 78)
    print()

    while True:
        try:
            choice = input(f"  Choose a model [1–{len(MODELS)}]: ").strip()
            idx = int(choice)
            if 1 <= idx <= len(MODELS):
                chosen = MODELS[idx - 1]
                _, _, cost = calc_cost(texts, chosen)
                print(f"\n  ✓ Selected: {chosen['label']} ({chosen['id']})")
                print(f"  ✓ Estimated cost: ${cost:.4f} USD\n")
                return chosen
            else:
                print(f"  Please enter a number between 1 and {len(MODELS)}.")
        except (ValueError, KeyboardInterrupt):
            print("\n  Aborted.")
            raise SystemExit(0)


# ---------------------------------------------------------------------------
# Claude API call
# ---------------------------------------------------------------------------

def analyze_comment(text: str, model_id: str) -> dict:
    """Send a single comment to Claude and return the parsed analysis dict."""
    payload = {
        "model": model_id,
        "max_tokens": 1000,
        "system": SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": ANALYSIS_PROMPT.format(comment=text.replace("'", "\\'")),
            }
        ],
    }

    response = requests.post(CLAUDE_API_URL, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()

    raw = data["content"][0]["text"].strip()

    # Strip markdown fences if model wraps despite instructions
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    return json.loads(raw)


def safe_analyze(text: str, comment_id: str, model_id: str) -> dict:
    """Analyze with error handling — returns a fallback dict on failure."""
    try:
        return analyze_comment(text, model_id)
    except (requests.HTTPError, json.JSONDecodeError, KeyError) as e:
        print(f"    [warn] Failed to analyze {comment_id}: {e}")
        return {
            "player_name": None,
            "country": None,
            "sentiment": "Neutral",
            "theme": "Analysis failed",
        }


# ---------------------------------------------------------------------------
# Comment walkers
# ---------------------------------------------------------------------------

def enrich_comment(comment: dict, model_id: str, delay: float) -> dict:
    """Analyze a comment and its replies in-place."""
    analysis = safe_analyze(comment.get("text", ""), comment.get("comment_id", ""), model_id)
    comment.update(analysis)

    for reply in comment.get("replies", []):
        analysis = safe_analyze(reply.get("text", ""), reply.get("comment_id", ""), model_id)
        reply.update(analysis)
        if delay:
            time.sleep(delay)

    return comment


def count_all_comments(data: dict) -> int:
    total = 0
    if "videos" in data:
        for video in data["videos"]:
            for c in video.get("comments", []):
                total += 1 + len(c.get("replies", []))
    elif "comments" in data:
        for c in data["comments"]:
            total += 1 + len(c.get("replies", []))
    return total


def process_comments(data: dict, model_id: str, delay: float) -> tuple[dict, int]:
    """Walk the JSON structure and enrich every comment and reply."""
    processed = 0

    if "videos" in data:
        for video in data["videos"]:
            title = video.get("title", video.get("video_id", "?"))[:60]
            comments = video.get("comments", [])
            print(f"\n  Video : {title}")
            print(f"  Comments: {len(comments):,}")

            for comment in comments:
                enrich_comment(comment, model_id, delay)
                processed += 1 + len(comment.get("replies", []))
                print(f"    Analyzed {processed} comments...", end="\r")
                if delay:
                    time.sleep(delay)

    elif "comments" in data:
        comments = data["comments"]
        print(f"  Comments: {len(comments):,}")

        for comment in comments:
            enrich_comment(comment, model_id, delay)
            processed += 1 + len(comment.get("replies", []))
            print(f"  Analyzed {processed} comments...", end="\r")
            if delay:
                time.sleep(delay)

    else:
        raise ValueError("Unrecognized JSON shape — expected 'comments' or 'videos' key.")

    return data, processed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze YouTube comments using Claude AI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("input", help="Input JSON file")
    parser.add_argument("-o", "--output", default=None, help="Output JSON file")
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "Skip interactive selection and use this model ID directly "
            "(e.g. claude-haiku-4-5)"
        ),
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.1,
        metavar="SECONDS",
        help="Delay between API calls in seconds (default: 0.1)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    output_path = (
        Path(args.output) if args.output
        else input_path.with_stem(input_path.stem + "_analyzed")
    )

    # Load data
    print(f"Loading: {input_path}")
    with open(input_path, encoding="utf-8") as f:
        data: dict[str, Any] = json.load(f)

    texts = collect_all_texts(data)

    # Model selection — interactive unless --model flag is passed
    if args.model:
        # Find in catalogue or treat as a raw model ID
        chosen = next((m for m in MODELS if m["id"] == args.model), None)
        if chosen is None:
            # Unknown model — create a minimal entry so the script still runs
            chosen = {"id": args.model, "label": args.model, "input": 0, "output": 0, "note": "custom"}
            print(f"  Using custom model: {args.model}")
        else:
            _, _, cost = calc_cost(texts, chosen)
            print(f"  Model   : {chosen['label']} ({chosen['id']})")
            print(f"  Est cost: ${cost:.4f} USD")
    else:
        chosen = print_model_menu(texts, args.delay)

    model_id = chosen["id"]

    # Analyze
    print("Starting analysis...\n")
    data, processed = process_comments(data, model_id, args.delay)

    # Tag output
    data["analyzed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    data["analysis_model"] = model_id

    # Save
    print(f"\n\nSaving to: {output_path}")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Done. {processed:,} comments analyzed.")


if __name__ == "__main__":
    main()
