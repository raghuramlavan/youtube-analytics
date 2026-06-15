"""
analyze_comments_deepseek.py
- Uses prompt from prompt.txt (Nike football fields)
- Parallel batch processing for speed
"""

import json
import time
import argparse
import os
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional
from datetime import datetime
import requests
from dotenv import load_dotenv

load_dotenv()

DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

# ── Prompts (aligned with prompt.txt) ─────────────────────────────────────────

SYSTEM_PROMPT = """You are analyzing YouTube comments about Nike football content.
For each comment, extract the following in JSON format:

{
  "comment_id": "<the comment id>",
  "sentiment": "positive" | "negative" | "neutral",
  "players_or_personalities": ["list of players/personalities mentioned"],
  "players_sentiment": ["positive"|"negative"|"neutral" for each player in same order],
  "nike_products_mentioned": ["list of Nike products mentioned"],
  "competitor_comparison": "positive" | "negative" | "neutral" | "none",
  "competitor_brands_mentioned": ["list of competitor brands like adidas, on, puma, etc."]
}

Rules:
- "sentiment" is about the overall comment tone toward the video/content.
- "players_or_personalities" includes footballers, coaches, influencers, celebrities mentioned.
- "players_sentiment" must be same length as "players_or_personalities" - indicates if comment is positive/negative/neutral about each person.
- "nike_products_mentioned" includes specific Nike products (e.g., Mercurial, Phantom, Air Max, etc.)
- "competitor_comparison" indicates if Nike is being compared to a competitor and whether that comparison is positive (Nike is better), negative (competitor is better), or neutral. "none" if no comparison.
- "competitor_brands_mentioned" includes brands like Adidas, Puma, On, New Balance, Under Armour, etc.

Return ONLY a JSON array of objects. No explanation or markdown."""

BATCH_ANALYSIS_PROMPT = """Analyze the following {batch_size} YouTube comments about Nike football content. Return a JSON array with {batch_size} objects, one per comment in the same order.

Comments to analyze:
{comments}

Output format: [{{"comment_id": "...", "sentiment": "positive|negative|neutral", "players_or_personalities": [...], "players_sentiment": [...], "nike_products_mentioned": [...], "competitor_comparison": "positive|negative|neutral|none", "competitor_brands_mentioned": [...]}}, ...]"""

EMPTY_ANALYSIS = {
    "comment_id": None,
    "sentiment": "neutral",
    "players_or_personalities": [],
    "players_sentiment": [],
    "nike_products_mentioned": [],
    "competitor_comparison": "none",
    "competitor_brands_mentioned": [],
}


# ── Checkpoint ─────────────────────────────────────────────────────────────────

class BatchAnalysisCheckpoint:
    def __init__(self, input_file: Path, output_file: Path, model_id: str, batch_size: int):
        self.input_file = input_file
        self.output_file = output_file
        self.model_id = model_id
        self.batch_size = batch_size
        self.checkpoint_file = output_file.with_suffix(".batch_checkpoint.json")
        self._lock = threading.Lock()  # thread-safe writes

    def save_checkpoint(self, completed_batches: int, completed_ids: List[str], data: dict):
        with self._lock:
            checkpoint = {
                "completed_batches": completed_batches,
                "completed_ids": completed_ids,
                "data": data,
                "model_id": self.model_id,
                "batch_size": self.batch_size,
                "input_file": str(self.input_file),
                "last_updated": datetime.now().isoformat(),
                "total_processed": len(completed_ids),
            }
            temp_file = self.checkpoint_file.with_suffix(".tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(checkpoint, f, ensure_ascii=False, indent=2)
            temp_file.replace(self.checkpoint_file)
        print(f"      💾 Checkpoint saved: {completed_batches} batches, {len(completed_ids)} comments")

    def load_checkpoint(self) -> Dict[str, Any]:
        if self.checkpoint_file.exists():
            try:
                with open(self.checkpoint_file, "r", encoding="utf-8") as f:
                    checkpoint = json.load(f)
                data = checkpoint.get("data")
                if data:
                    count = self.count_analyzed_comments(data)
                    print(f"  ✓ Found checkpoint with {count} analyzed comments")
                    print(f"  ✓ Completed batches: {checkpoint.get('completed_batches', 0)}")
                    return checkpoint
                else:
                    print("  ⚠ Checkpoint has no data. Starting fresh.")
            except Exception as e:
                print(f"  ⚠ Could not load checkpoint: {e}")
        return {"completed_batches": 0, "completed_ids": [], "data": None}

    def count_analyzed_comments(self, data: dict) -> int:
        count = 0
        def recurse(obj):
            nonlocal count
            if isinstance(obj, dict):
                if "text" in obj and obj.get("players_or_personalities") is not None:
                    count += 1
                for v in obj.values():
                    recurse(v)
            elif isinstance(obj, list):
                for item in obj:
                    recurse(item)
        recurse(data)
        return count

    def clear_checkpoint(self):
        if self.checkpoint_file.exists():
            self.checkpoint_file.unlink()
            print("  ✓ Checkpoint removed")


# ── Helpers ────────────────────────────────────────────────────────────────────

def collect_comments_with_paths(data: dict) -> List[Tuple[Dict, str]]:
    results = []

    def process_comment(comment, path):
        results.append((comment, path))
        for i, reply in enumerate(comment.get("replies", [])):
            process_comment(reply, f"{path}.replies[{i}]")

    if "videos" in data:
        for vi, video in enumerate(data["videos"]):
            for ci, c in enumerate(video.get("comments", [])):
                process_comment(c, f"videos[{vi}].comments[{ci}]")
    elif "comments" in data:
        for ci, c in enumerate(data["comments"]):
            process_comment(c, f"comments[{ci}]")

    return results


def filter_short_comments(comments_with_paths: List[Tuple[Dict, str]], min_len: int = 3) -> List[Tuple[Dict, str]]:
    kept, skipped = [], 0
    for comment, path in comments_with_paths:
        text = (comment.get("text") or "").strip()
        if len(text) < min_len:
            comment["_skipped_analysis"] = True
            skipped += 1
        else:
            kept.append((comment, path))
    if skipped:
        print(f"  🔍 Skipped {skipped} empty/short comments (< {min_len} chars)")
    return kept


def get_api_key(args_api_key: Optional[str] = None) -> str:
    api_key = args_api_key or os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise ValueError("DeepSeek API key not found. Set DEEPSEEK_API_KEY in .env file")
    return api_key


# ── API call ───────────────────────────────────────────────────────────────────

MAX_RETRIES = 5
BASE_BACKOFF = 2  # seconds


def _call_api(comment_texts: List[str], model_id: str, api_key: str) -> List[Dict]:
    """Single API call — no retry logic. Raises on any error."""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    comments_list = []
    for i, text in enumerate(comment_texts, 1):
        # Truncate long comments to keep output tokens manageable
        text_clean = text[:300].replace('"', '\\"').replace("\n", " ")
        comments_list.append(f"{i}. {text_clean}")

    batch_prompt = BATCH_ANALYSIS_PROMPT.format(
        batch_size=len(comment_texts),
        comments="\n".join(comments_list),
    )

    payload = {
        "model": model_id,
        "max_tokens": 8192,   # DeepSeek max — avoids truncated JSON
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": batch_prompt},
        ],
    }

    response = requests.post(DEEPSEEK_API_URL, json=payload, headers=headers, timeout=180)
    response.raise_for_status()
    raw = response.json()["choices"][0]["message"]["content"].strip()

    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    analyses = json.loads(raw)  # raises json.JSONDecodeError on truncation
    if not isinstance(analyses, list):
        raise ValueError(f"Expected JSON array, got {type(analyses)}")
    return analyses


def analyze_batch(comment_texts: List[str], batch_idx: int, model_id: str, api_key: str) -> List[Dict]:
    """
    Send a batch to DeepSeek with:
    - Exponential backoff retry for rate limits (429/529)
    - Auto-split into halves on JSON parse errors (truncated response)
    - Pad/trim result to exact batch size
    """

    def _with_retry(texts: List[str], depth: int = 0) -> List[Dict]:
        """Retry with backoff; on JSON error split in half (max depth 3)."""
        for attempt in range(MAX_RETRIES):
            try:
                return _call_api(texts, model_id, api_key)

            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else 0
                if status in (429, 529):
                    wait = BASE_BACKOFF ** (attempt + 1)
                    print(f"   ⚠ Rate limit (HTTP {status}) — waiting {wait}s before retry {attempt+1}/{MAX_RETRIES}...")
                    time.sleep(wait)
                    continue
                raise  # other HTTP errors bubble up

            except (json.JSONDecodeError, ValueError) as e:
                if depth < 3 and len(texts) > 1:
                    # Response was truncated — split batch in half and retry each half
                    mid = len(texts) // 2
                    print(f"   ⚠ JSON parse error on {len(texts)} comments — splitting into {mid}+{len(texts)-mid} and retrying...")
                    left = _with_retry(texts[:mid], depth + 1)
                    right = _with_retry(texts[mid:], depth + 1)
                    return left + right
                else:
                    # Single comment still fails — return empty analysis
                    print(f"   ⚠ JSON error on single comment (batch {batch_idx}) — using empty analysis: {e}")
                    return [dict(EMPTY_ANALYSIS) for _ in texts]

            except Exception:
                if attempt == MAX_RETRIES - 1:
                    raise
                wait = BASE_BACKOFF ** (attempt + 1)
                print(f"   ⚠ API error — retrying in {wait}s (attempt {attempt+1}/{MAX_RETRIES})...")
                time.sleep(wait)

        raise RuntimeError(f"Batch {batch_idx} failed after {MAX_RETRIES} retries")

    analyses = _with_retry(comment_texts)

    # Pad / trim to exact batch size
    while len(analyses) < len(comment_texts):
        analyses.append(dict(EMPTY_ANALYSIS))
    return analyses[:len(comment_texts)]


# ── Print helpers ──────────────────────────────────────────────────────────────

def print_analysis_output(comment: Dict, analysis: Dict, batch_idx: int, comment_num: int):
    text = comment.get("text", "")
    preview = text[:80] + "..." if len(text) > 80 else text

    sentiment = analysis.get("sentiment", "neutral")
    players = analysis.get("players_or_personalities", [])
    players_sentiment = analysis.get("players_sentiment", [])
    products = analysis.get("nike_products_mentioned", [])
    comp_cmp = analysis.get("competitor_comparison", "none")
    comp_brands = analysis.get("competitor_brands_mentioned", [])

    s_emoji = {"positive": "😊", "negative": "😠", "neutral": "😐"}.get(sentiment, "💬")

    print(f"\n{'─'*80}")
    print(f"📝 COMMENT #{comment_num} (Batch {batch_idx})")
    print(f"{'─'*80}")
    print(f"💬 Text: {preview}")
    print(f"\n🤖 ANALYSIS:")
    print(f"   {s_emoji} Sentiment: {sentiment}")

    if players:
        parts = []
        for p, ps in zip(players, players_sentiment if players_sentiment else ["-"] * len(players)):
            p_emoji = {"positive": "👍", "negative": "👎", "neutral": "➖"}.get(ps, "")
            parts.append(f"{p} ({p_emoji}{ps})")
        print(f"   👤 Players/Personalities: {', '.join(parts)}")
    else:
        print(f"   👤 Players/Personalities: —")

    print(f"   👟 Nike Products: {', '.join(products) if products else '—'}")

    c_emoji = {"positive": "✅", "negative": "❌", "neutral": "➖", "none": ""}.get(comp_cmp, "")
    print(f"   ⚔️  Competitor Comparison: {c_emoji} {comp_cmp}")
    print(f"   🏢 Competitor Brands: {', '.join(comp_brands) if comp_brands else '—'}")
    print(f"{'─'*80}")


def print_batch_summary(batch_idx: int, total_batches: int, batch: List, analyses: List[Dict], elapsed: float):
    n = len(batch)
    sentiments = [a.get("sentiment", "neutral") for a in analyses]
    players_found = sum(1 for a in analyses if a.get("players_or_personalities"))
    products_found = sum(1 for a in analyses if a.get("nike_products_mentioned"))
    comp_found = sum(1 for a in analyses if a.get("competitor_comparison") not in ("none", None))

    print(f"\n{'='*80}")
    print(f"✅ BATCH {batch_idx}/{total_batches} COMPLETE  ⏱ {elapsed:.1f}s")
    print(f"{'='*80}")
    print(f"   📊 Comments: {n}  |  👤 Players: {players_found}  |  👟 Products: {products_found}  |  ⚔️ Competitor: {comp_found}")
    print(f"   📈 Sentiment — positive: {sentiments.count('positive')}  negative: {sentiments.count('negative')}  neutral: {sentiments.count('neutral')}")
    print(f"{'='*80}\n")


def print_final_stats(all_analyses: List[Dict]):
    n = len(all_analyses)
    if not n:
        print("   No comments analyzed.")
        return

    sentiments = [a["analysis"].get("sentiment", "neutral") for a in all_analyses]
    all_players = [p for a in all_analyses for p in (a["analysis"].get("players_or_personalities") or [])]
    all_products = [p for a in all_analyses for p in (a["analysis"].get("nike_products_mentioned") or [])]
    all_brands = [b for a in all_analyses for b in (a["analysis"].get("competitor_brands_mentioned") or [])]
    comp_cmps = [a["analysis"].get("competitor_comparison") for a in all_analyses]

    print(f"\n📈 FINAL STATISTICS  (total: {n} comments)")
    print(f"   😊 Positive : {sentiments.count('positive'):>5}  ({sentiments.count('positive')*100//n}%)")
    print(f"   😠 Negative : {sentiments.count('negative'):>5}  ({sentiments.count('negative')*100//n}%)")
    print(f"   😐 Neutral  : {sentiments.count('neutral'):>5}  ({sentiments.count('neutral')*100//n}%)")
    print(f"   👤 Comments with players  : {sum(1 for a in all_analyses if a['analysis'].get('players_or_personalities'))}")
    print(f"   👟 Comments with products : {sum(1 for a in all_analyses if a['analysis'].get('nike_products_mentioned'))}")
    print(f"   ⚔️  Competitor comparisons: {sum(1 for c in comp_cmps if c not in ('none', None))}")

    if all_players:
        print(f"\n   🏆 Top 10 Players/Personalities:")
        for name, cnt in Counter(all_players).most_common(10):
            print(f"      • {name}: {cnt}")
    if all_products:
        print(f"\n   👟 Top Nike Products:")
        for prod, cnt in Counter(all_products).most_common(10):
            print(f"      • {prod}: {cnt}")
    if all_brands:
        print(f"\n   🏢 Top Competitor Brands:")
        for brand, cnt in Counter(all_brands).most_common(10):
            print(f"      • {brand}: {cnt}")
    print(f"{'='*80}\n")


# ── Core processing ────────────────────────────────────────────────────────────

def process_single_batch(
    batch: List[Tuple[Dict, str]],
    batch_idx: int,
    total_batches: int,
    model_id: str,
    api_key: str,
    verbose: bool,
    completed_ids: set,
    completed_ids_lock: threading.Lock,
    data: dict,
    checkpoint: BatchAnalysisCheckpoint,
) -> Tuple[List[Dict], bool]:
    """Analyse one batch; thread-safe. Returns (list_of_analysis_dicts, success)."""
    t0 = time.time()
    comment_texts = [c.get("text", "") for c, _ in batch]

    try:
        analyses = analyze_batch(comment_texts, batch_idx, model_id, api_key)
    except Exception as e:
        print(f"\n❌ Batch {batch_idx}/{total_batches} FAILED: {e}")
        return [], False

    batch_results = []
    with completed_ids_lock:
        for (comment, _), analysis in zip(batch, analyses):
            comment["sentiment"] = analysis.get("sentiment", "neutral")
            comment["players_or_personalities"] = analysis.get("players_or_personalities", [])
            comment["players_sentiment"] = analysis.get("players_sentiment", [])
            comment["nike_products_mentioned"] = analysis.get("nike_products_mentioned", [])
            comment["competitor_comparison"] = analysis.get("competitor_comparison", "none")
            comment["competitor_brands_mentioned"] = analysis.get("competitor_brands_mentioned", [])
            comment["_analyzed_at"] = datetime.now().isoformat()
            comment["_batch_id"] = batch_idx
            completed_ids.add(comment["_analysis_id"])
            batch_results.append({"text": comment.get("text", ""), "analysis": analysis, "batch": batch_idx})

    checkpoint.save_checkpoint(batch_idx, list(completed_ids), data)
    elapsed = time.time() - t0

    if verbose:
        for i, ((comment, _), analysis) in enumerate(zip(batch, analyses), 1):
            print_analysis_output(comment, analysis, batch_idx, i)

    print_batch_summary(batch_idx, total_batches, batch, analyses, elapsed)
    return batch_results, True


def process_with_checkpoint(
    data: dict,
    model_id: str,
    api_key: str,
    batch_size: int,
    delay: float,
    checkpoint: BatchAnalysisCheckpoint,
    force_restart: bool = False,
    verbose: bool = False,
    workers: int = 3,
    min_comment_len: int = 3,
):
    # ── Load checkpoint ──
    if not force_restart:
        cp = checkpoint.load_checkpoint()
        if cp.get("data") and cp.get("completed_ids"):
            data = cp["data"]
            completed_ids = set(cp["completed_ids"])
            completed_batches = cp["completed_batches"]
            print(f"\n  ✅ Resuming: {len(completed_ids)} comments already done")
        else:
            completed_ids, completed_batches = set(), 0
    else:
        completed_ids, completed_batches = set(), 0
        checkpoint.clear_checkpoint()

    # ── Collect + filter ──
    all_comments = collect_comments_with_paths(data)
    total = len(all_comments)

    # Assign IDs
    for comment, _ in all_comments:
        cid = comment.get("comment_id") or hashlib.md5(comment.get("text", "").encode()).hexdigest()[:16]
        comment["_analysis_id"] = cid

    unprocessed = [(c, p) for c, p in all_comments
                   if c["_analysis_id"] not in completed_ids and not c.get("_skipped_analysis")]
    unprocessed = filter_short_comments(unprocessed, min_len=min_comment_len)

    # ── Build batches ──
    batches, cur = [], []
    for item in unprocessed:
        cur.append(item)
        if len(cur) >= batch_size:
            batches.append(cur); cur = []
    if cur:
        batches.append(cur)

    if not batches:
        print(f"\n  ✅ All {total} comments already processed!")
        return data, total

    remaining = sum(len(b) for b in batches)
    print(f"\n{'='*80}")
    print(f"📊 PROCESSING SUMMARY")
    print(f"{'='*80}")
    print(f"   Total comments in file : {total}")
    print(f"   Already analyzed       : {len(completed_ids)}")
    print(f"   To process             : {remaining}")
    print(f"   Batches                : {len(batches)}")
    print(f"   Batch size             : {batch_size}")
    print(f"   Parallel workers       : {workers}")
    print(f"{'='*80}\n")

    completed_ids_lock = threading.Lock()
    all_analyses: List[Dict] = []
    all_analyses_lock = threading.Lock()

    if workers <= 1:
        # Sequential
        for i, batch in enumerate(batches, start=1):
            results, ok = process_single_batch(
                batch, i, len(batches), model_id, api_key, verbose,
                completed_ids, completed_ids_lock, data, checkpoint,
            )
            if results:
                all_analyses.extend(results)
            if delay:
                time.sleep(delay)
    else:
        # Parallel
        print(f"⚡ Parallel mode — {workers} concurrent workers\n")
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    process_single_batch,
                    batch, completed_batches + i + 1, completed_batches + len(batches),
                    model_id, api_key, verbose,
                    completed_ids, completed_ids_lock, data, checkpoint,
                ): i
                for i, batch in enumerate(batches)
            }
            for future in as_completed(futures):
                try:
                    results, ok = future.result()
                    if results:
                        with all_analyses_lock:
                            all_analyses.extend(results)
                except Exception as e:
                    print(f"⚠ Worker error: {e}")

    print(f"\n{'='*80}")
    print(f"🎉 ANALYSIS COMPLETE!")
    print(f"{'='*80}")
    print_final_stats(all_analyses)

    return data, len(completed_ids)


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Analyze YouTube comments with DeepSeek (Nike football prompt)")
    parser.add_argument("input", help="Input JSON file from youtube_broad_search.py or youtube_search_comments.py")
    parser.add_argument("-o", "--output", default=None, help="Output JSON file")
    parser.add_argument("--batch-size", type=int, default=25, help="Comments per batch (default: 25)")
    parser.add_argument("--workers", type=int, default=2, help="Parallel API workers (default: 2, 1=sequential)")
    parser.add_argument("--delay", type=float, default=0.0, help="Delay between batches in seconds (default: 0)")
    parser.add_argument("--api-key", default=None, help="DeepSeek API key (overrides .env)")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint")
    parser.add_argument("--force-restart", action="store_true", help="Ignore existing checkpoint")
    parser.add_argument("--verbose", action="store_true", help="Print every comment's analysis")
    parser.add_argument("--min-length", type=int, default=3, help="Min comment length to analyze (default: 3)")
    args = parser.parse_args()

    api_key = get_api_key(args.api_key)

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    output_path = Path(args.output) if args.output else input_path.with_stem(input_path.stem + "_analyzed")

    print(f"\n📁 Loading: {input_path}")
    with open(input_path, encoding="utf-8") as f:
        data = json.load(f)

    checkpoint = BatchAnalysisCheckpoint(input_path, output_path, "deepseek-chat", args.batch_size)

    print("\n" + "="*60)
    print("🚀 STARTING DEEPSEEK BATCH ANALYSIS")
    print("   Prompt: Nike football (prompt.txt fields)")
    print("="*60)

    try:
        data, processed = process_with_checkpoint(
            data=data,
            model_id="deepseek-chat",
            api_key=api_key,
            batch_size=args.batch_size,
            delay=args.delay,
            checkpoint=checkpoint,
            force_restart=args.force_restart,
            verbose=args.verbose,
            workers=args.workers,
            min_comment_len=args.min_length,
        )
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted! Progress saved to checkpoint. Re-run with --resume to continue.")
        return

    data["analyzed_at"] = datetime.now().isoformat()
    data["analysis_model"] = "deepseek-chat"
    data["analysis_provider"] = "DeepSeek"
    data["analysis_prompt"] = "prompt.txt (Nike football)"
    data["total_analyzed"] = processed

    print(f"\n💾 Saving: {output_path}")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n✅ DONE — {processed} comments analyzed → {output_path}")
    checkpoint.clear_checkpoint()


if __name__ == "__main__":
    main()
