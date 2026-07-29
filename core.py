"""
B-Roll Analyzer — deployable core logic.

Converted from a Colab notebook:
  - No `google.colab.drive` mount — works on any local/server folder.
  - API keys come from the GEMINI_API_KEYS env var (comma-separated),
    never hardcoded in source.
  - Everything else (key rotation, model fallback, resumable CSV,
    JSON schema) is unchanged from the original notebook.
"""

import os
import json
import time
import random
import csv
from pathlib import Path

from google import genai
from google.genai import types
from moviepy import VideoFileClip

# ============================================================
# CONFIG (env-var driven — set these in your platform's secrets/env panel)
# ============================================================
GEMINI_API_KEYS = [k.strip() for k in os.environ.get("GEMINI_API_KEYS", "").split(",") if k.strip()]

MODEL_FALLBACKS = {
    "video_analysis": [
        "gemini-3.5-flash",
        "gemini-3.6-flash",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
    ],
}

MAX_RETRIES_PER_KEY_ROUND = 4
RATE_LIMIT_SLEEP_SECS = 20
RATE_LIMIT_BACKOFF = 1.6
FILE_PROCESSING_TIMEOUT = 300

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}

BROLL_ANALYSIS_PROMPT = """
You are cataloguing a short (5-6 second) narrator b-roll clip for a video
editing pipeline. Lip-sync and audio are IRRELEVANT — only visuals matter:
gestures, body language, pacing, camera framing, and setting.

Watch the whole clip and return ONE JSON object describing it — do not split
it into scenes or return timestamps, this clip is already a single unit.

Return:
- description: ONE natural sentence a video editor could scan at a glance to
  judge if this clip fits a moment in a story, in this style: "Hands open,
  moving slowly up and down, bookshelf in the background, calm pacing."
  Mention the hand/body action, the background/setting, and the pace.
- subjects: who/what is on screen
- actions: short verb phrases for what's happening
- hand_movements: specific hand/finger/arm gestures, empty list if hands aren't visible
- body_movements: posture/movement of the body (leans forward, turns, sits still...)
- facial_expressions: visible expressions, empty list if face isn't visible/clear
- camera: {"shot_type": e.g. "Medium Close-Up", "angle": e.g. "Eye Level",
  "movement": e.g. "Slow Push In" or "Static", "zoom": "None" or a description}
- gesture_pace: one of "slow", "medium", "fast"
- lighting: brief description
- environment: brief setting description
- objects: notable objects visible
- emotion: dominant mood/emotion conveyed
- keywords: 3-8 short tags useful for search/matching
- broll_use_cases: 1-4 short phrases for what kind of narration moment this suits
- importance: integer 1-5, how broadly useful/reusable this clip is as generic b-roll

Return ONLY JSON in this exact shape, nothing else:
{
  "description": "...",
  "subjects": ["..."],
  "actions": ["..."],
  "hand_movements": ["..."],
  "body_movements": ["..."],
  "facial_expressions": ["..."],
  "camera": {"shot_type": "...", "angle": "...", "movement": "...", "zoom": "..."},
  "gesture_pace": "slow",
  "lighting": "...",
  "environment": "...",
  "objects": ["..."],
  "emotion": "...",
  "keywords": ["..."],
  "broll_use_cases": ["..."],
  "importance": 3
}
"""


def _model_state_path(work_dir):
    return os.path.join(work_dir, "broll_model_state.json")


def _load_model_state(work_dir):
    p = _model_state_path(work_dir)
    if os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_model_state(work_dir, state):
    with open(_model_state_path(work_dir), "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


class GeminiKeyManager:
    """Same rollback strategy as the original notebook: random key per
    attempt, rotate away from rate-limited keys, disable invalid keys for
    the session, drop models that 404, advance the fallback chain after
    repeated rate-limit rounds, and persist chain position to disk."""

    def __init__(self, api_keys, model_fallbacks, work_dir):
        if not api_keys:
            raise ValueError(
                "No Gemini API keys provided. Set GEMINI_API_KEYS as an "
                "environment variable (comma-separated), e.g. "
                "GEMINI_API_KEYS=key1,key2,key3"
            )
        self.api_keys = api_keys
        self.disabled_keys = set()
        self._clients = {k: genai.Client(api_key=k) for k in api_keys}
        self.model_fallbacks = model_fallbacks
        self.work_dir = work_dir
        self.state = _load_model_state(work_dir)

    def current_model(self, task):
        chain = self.model_fallbacks[task]
        idx = self.state.get(task, {}).get("index", 0)
        idx = min(idx, len(chain) - 1)
        return chain[idx], idx

    def _advance_model(self, task):
        chain = self.model_fallbacks[task]
        _, idx = self.current_model(task)
        if idx + 1 >= len(chain):
            raise RuntimeError(
                f"All fallback models exhausted for task '{task}': {chain}. "
                "Every model either 404'd or stayed rate-limited/quota-exhausted."
            )
        new_idx = idx + 1
        self.state[task] = {"index": new_idx, "model": chain[new_idx]}
        _save_model_state(self.work_dir, self.state)

    def _random_key(self, exclude):
        live = [k for k in self.api_keys if k not in self.disabled_keys]
        pool = [k for k in live if k not in exclude] or live or self.api_keys
        return random.choice(pool)

    @staticmethod
    def _classify(err):
        msg = str(err)
        if "404" in msg or "NOT_FOUND" in msg:
            return "not_found"
        if any(s in msg for s in ("403", "401", "PERMISSION_DENIED", "UNAUTHENTICATED", "denied access")):
            return "key_invalid"
        if any(s in msg.lower() for s in ("429", "resource_exhausted", "rate limit", "quota", "unavailable", "503")):
            return "rate_limit"
        return "other"

    def call(self, task, fn):
        while True:
            model, _ = self.current_model(task)
            tried = set()
            sleep_secs = RATE_LIMIT_SLEEP_SECS
            rounds_exhausted = 0
            attempt = 0

            while attempt < MAX_RETRIES_PER_KEY_ROUND:
                attempt += 1
                key = self._random_key(tried)
                tried.add(key)
                try:
                    return fn(self._clients[key], model)
                except Exception as err:
                    kind = self._classify(err)

                    if kind == "not_found":
                        break

                    if kind == "key_invalid":
                        self.disabled_keys.add(key)
                        if len(self.disabled_keys) >= len(self.api_keys):
                            raise RuntimeError(f"All API keys are invalid/denied: {err}") from err
                        attempt -= 1
                        continue

                    if kind == "rate_limit":
                        live_count = len(self.api_keys) - len(self.disabled_keys)
                        if len({k for k in tried if k not in self.disabled_keys}) >= max(live_count, 1):
                            rounds_exhausted += 1
                            tried = set()
                            if rounds_exhausted >= 2:
                                break
                            time.sleep(sleep_secs)
                            sleep_secs *= RATE_LIMIT_BACKOFF
                        continue

                    time.sleep(2)

            self._advance_model(task)


def _upload_and_wait(client, video_path, timeout=None):
    timeout = timeout or FILE_PROCESSING_TIMEOUT
    remote = client.files.upload(file=video_path)
    start = time.time()
    while getattr(remote.state, "name", remote.state) == "PROCESSING":
        if time.time() - start > timeout:
            raise TimeoutError(f"Gemini took too long to process {video_path}")
        time.sleep(3)
        remote = client.files.get(name=remote.name)
    state_name = getattr(remote.state, "name", remote.state)
    if state_name == "FAILED":
        raise RuntimeError(f"Gemini failed to ingest {video_path}")
    return remote


def analyze_broll_video(video_path, key_manager):
    def _fn(client, model):
        remote = _upload_and_wait(client, video_path)
        try:
            response = client.models.generate_content(
                model=model,
                contents=[remote, BROLL_ANALYSIS_PROMPT],
                config=types.GenerateContentConfig(response_mime_type="application/json"),
            )
            return json.loads(response.text)
        finally:
            try:
                client.files.delete(name=remote.name)
            except Exception:
                pass

    return key_manager.call("video_analysis", _fn)


def _load_processed_video_names(csv_path):
    if not os.path.isfile(csv_path):
        return set()
    with open(csv_path, "r", newline="", encoding="utf-8") as f:
        return {row["video"] for row in csv.DictReader(f) if row.get("video")}


def _append_csv_row(csv_path, video_rel, description):
    is_new = not os.path.isfile(csv_path)
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["video", "description"])
        writer.writerow([video_rel, description])


def _append_metadata(metadata_path, entry):
    data = []
    if os.path.isfile(metadata_path):
        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = []
    data.append(entry)
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def materialize_clip(video_path, analysis, des_dir, csv_path, metadata_path):
    video_name = Path(video_path).name

    with VideoFileClip(video_path) as vc:
        real_duration = round(vc.duration, 2)

    cache_path = os.path.join(des_dir, ".duration_cache.json")
    cache = {}
    if os.path.isfile(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}
    cache[video_name] = real_duration
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)

    _append_csv_row(csv_path, video_name, json.dumps(analysis, ensure_ascii=False))
    _append_metadata(metadata_path, {"video": video_name, "duration": real_duration, **analysis})
    return video_name


def process_broll_folder(source_dir, log=print):
    """Analyzes every unprocessed clip in source_dir. Returns the CSV path.
    log: a callable(str) for progress messages (print, or a UI callback)."""
    source_dir = str(source_dir)
    des_dir = os.path.join(source_dir, "des")
    os.makedirs(des_dir, exist_ok=True)
    csv_path = os.path.join(des_dir, "broll_auto.csv")
    metadata_path = os.path.join(des_dir, "broll_metadata.json")

    key_manager = GeminiKeyManager(GEMINI_API_KEYS, MODEL_FALLBACKS, des_dir)

    videos = sorted(
        p for p in Path(source_dir).iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    )
    if not videos:
        log(f"⚠️ no video files found under {source_dir}")
        return csv_path

    processed = _load_processed_video_names(csv_path)
    log(f"Found {len(videos)} clip(s). {len(processed)} already processed — resuming.")

    for video_path in videos:
        key = video_path.name
        if key in processed:
            log(f"✔ {key} — already in CSV, skipping")
            continue

        log(f"🎥 Analyzing {key}...")
        try:
            analysis = analyze_broll_video(str(video_path), key_manager)
        except Exception as err:
            log(f"❌ analysis failed for {key}: {err} — leaving unprocessed")
            continue

        materialize_clip(str(video_path), analysis, des_dir, csv_path, metadata_path)
        log(f"✔ wrote row for {key}")

    log(f"🎬 Done. CSV: {csv_path}")
    return csv_path
