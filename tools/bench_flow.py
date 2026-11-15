"""Supplied-judgment CLI comparison, never an autonomous language evaluation.

Every engine gets the same real subprocess commands, raw fixtures and guards.
Media fixture creation is outside elapsed; prepare through final read-back is in.
"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ENGLISH = [
    "Close the door.",
    "Open the gate.",
    "Bring the bag.",
    "Wait here.",
    "- I know.\n- Then go.",
    "<i>Leave it.</i>",
    "Stay!",
    "Where?",
]
KOREAN = [
    "문을 닫아.",
    "문을 열어.",
    "가방을 가져와.",
    "여기서 기다려.",
    "- 알아.\n- 그럼 가.",
    "<i>놔둬.</i>",
    "기다려!",
    "어디?",
]


def stamp(t):
    return f"{t // 3600:02}:{t // 60 % 60:02}:{t % 60:02},000"


def run(label, engine, cues, episodes=1):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        media = root / "reference.mkv"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"color=size=16x16:rate=1:duration={cues + 10}",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                str(media),
            ],
            check=True,
        )
        inputs = {}
        for language, words in (("en", ENGLISH), ("ko", KOREAN)):
            path = root / (language + ".srt")
            path.write_text(
                "\n\n".join(
                    f"{i}\n{stamp(i)} --> {stamp(i + 1)}\n" + words[i % len(words)]
                    for i in range(1, cues + 1)
                )
                + "\n"
                + (
                    f"\n{cues + 1}\n{stamp(cues + 1)} --> {stamp(cues + 2)}\n화면\n"
                    if language == "ko"
                    else ""
                )
            )
            inputs[language] = path
        request = media
        if episodes > 1:
            request = root / "videos"
            request.mkdir()
            for i in range(episodes):
                shutil.copyfile(media, request / f"episode-{i + 1:02}.mkv")
        work = root / "work"
        calls = 0
        stdout = 0
        started = time.perf_counter()

        def cli(*args):
            nonlocal calls, stdout
            result = subprocess.run(
                [sys.executable, "-B", str(engine), *map(str, args)],
                capture_output=True,
            )
            calls += 1
            stdout += len(result.stdout)
            if result.returncode:
                raise RuntimeError(result.stderr.decode())
            return json.loads(result.stdout)

        def submit(item, response):
            path = root / "response.json"
            path.write_text(json.dumps(response, ensure_ascii=False))
            return cli("submit", item, path)

        prepared = cli("prepare", request, "--work", work)
        items = (
            [Path(row["project"]) for row in prepared["items"]]
            if episodes > 1
            else [work]
        )
        for item in items:
            for language, path in inputs.items():
                submit(
                    item,
                    {
                        "kind": "source",
                        "language": language,
                        "origin": "external" if language == "en" else "sidecar",
                        "path": str(path),
                        "automatic": False,
                        "verified_language": True,
                        "complete": True,
                        "release_match": True,
                        "ordinary": True,
                        "generated": False,
                        "translator_status": "unknown",
                        "provenance": {"supplied_synthetic_fixture": True},
                        "evidence": "Supplied mechanical fixture judgment, not autonomous verification.",
                        "presentation_evidence": {
                            "checked": "Supplied synthetic SRT layout.",
                            "preserved": "SRT",
                            "limitations": [],
                            "resolved": True,
                        },
                        "profile": "ordinary",
                        "selection_reason": "Only supplied fixture.",
                        "fallback_reason": "Fixture has no embedded subtitles.",
                        "candidate_assessments": [
                            {
                                "origin": "external",
                                "path": str(path),
                                "profile": "ordinary",
                                "usable": True,
                                "evidence": "Supplied fixture.",
                            }
                        ],
                    },
                )
        view = cli("view", work, "--role", "study", "--worker", "coordinator")
        response = view["submit"]
        response["works"][0].update(
            identity={"title": "Complete supplied synthetic utterance cycle"},
            facts={
                "overview": {
                    "text": "Supplied complete script, an eight-utterance cycle and collected screen text.",
                    "status": "established",
                    "global": False,
                    "evidence": ["script"],
                }
            },
            evidence={
                "script": {
                    "kind": "user",
                    "ref": "supplied complete synthetic script",
                    "access": "body",
                    "observation": "Synthetic supplied utterances and decisions; no web, video or audio perception.",
                }
            },
            decision="ready",
        )
        submit(work, response)
        for number, item in enumerate(items):
            author = f"worker-{number}"
            reviewer = f"worker-{(number + 1) % episodes}" if episodes > 1 else "reader"

            def view(role, ids=None):
                return cli(
                    "view",
                    item,
                    "--role",
                    role,
                    "--worker",
                    author if role == "author" else reviewer,
                    *(["--ids", ids] if ids else ["--pending"]),
                )

            count = 0
            while True:
                packet = view("author")
                response = packet["submit"]
                response["default_kind"] = "dialogue"
                for row in response["rows"]:
                    n = int(row["id"][1:])
                    row.update(text=KOREAN[n % len(KOREAN)], ko=["K" + str(n)])
                    count += 1
                if not packet["remaining"]:
                    response["supplements"] = [
                        {
                            "id": "S1",
                            "origin": "collected",
                            "kind": "screen_text",
                            "start": (cues + 1) * 1000,
                            "end": (cues + 2) * 1000,
                            "text": "임시 화면",
                            "links": ["K" + str(cues + 1)],
                            "reason": "Retain actual supplied Korean-only screen text.",
                        }
                    ]
                    response["impact"] = {
                        "scope": "local",
                        "ids": ["K" + str(cues + 1)],
                        "reason": "Supplied screen-text addition.",
                    }
                submit(item, response)
                if not packet["remaining"]:
                    break
            assert count == cues
            submit(item, {"kind": "seal", "worker": author, "unresolved": []})
            while True:
                packet = view("reviewer")
                submit(item, packet["submit"])
                if not packet["remaining"]:
                    break
            response = view("reviewer", f"E1,S1,K{cues + 1}")["submit"]
            response["corrections"] = [{"id": "E1", "text": "문 좀 열어."}]
            response["supplements"] = [
                {
                    "id": "S1",
                    "origin": "collected",
                    "kind": "screen_text",
                    "start": (cues + 1) * 1000,
                    "end": (cues + 2) * 1000,
                    "text": "화면",
                    "links": [f"K{cues + 1}"],
                    "reason": "Restore supplied collected-only screen text exactly.",
                }
            ]
            response["impact"] = {
                "scope": "local",
                "ids": ["E1", "S1", f"K{cues + 1}"],
                "reason": "Supplied local wording correction.",
            }
            submit(item, response)
            while True:
                packet = view("reviewer")
                submit(item, packet["submit"])
                if not packet["remaining"]:
                    break
            submit(item, {"kind": "approval", "worker": reviewer})
        placed = cli("finalize", work)
        results = placed["results"] if episodes > 1 else [placed]
        for result in results:
            if "result" in result:
                result = result["result"]
            assert (
                hashlib.sha256(Path(result["placed"]).read_bytes()).hexdigest()
                == result["sha256"]
            )
        status = cli("status", work)
        statuses = status["items"] if episodes > 1 else [status]
        assert all(row["approved"] and row["pending_count"] == 0 for row in statuses)
        hashes = [row["approved_sha256"] for row in statuses]
        return {
            "engine": label,
            "english_cues_per_episode": cues,
            "korean_cues_per_episode": cues + 1,
            "fixture_version": "collected-only-supplement-v2",
            "episodes": episodes,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            "calls": calls,
            "stdout_bytes": stdout,
            "repeat_presentations": sum(
                row["metrics"].get("repeat_presentations", 0) for row in statuses
            ),
            "cue_presentations": sum(
                row["metrics"].get("cue_presentations", 0) for row in statuses
            ),
            "output_hashes": hashes,
            "model_usage": "not_measured",
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--engine",
        action="append",
        required=True,
        help="LABEL=PATH, same required workflow for all engines",
    )
    parser.add_argument("--size", action="append", type=int)
    parser.add_argument("--episodes", type=int, default=1)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--no-warmup", action="store_true")
    args = parser.parse_args()
    engines = [
        (label, Path(path).resolve())
        for label, path in (item.split("=", 1) for item in args.engine)
    ]
    for size in args.size or [1000, 4000]:
        if not args.no_warmup:
            for label, path in engines:
                print(
                    json.dumps(
                        {"sample": "warmup", **run(label, path, size, args.episodes)}
                    ),
                    flush=True,
                )
        for repeat in range(args.runs):
            for label, path in engines if repeat % 2 == 0 else list(reversed(engines)):
                print(
                    json.dumps(
                        {"sample": repeat, **run(label, path, size, args.episodes)}
                    ),
                    flush=True,
                )


if __name__ == "__main__":
    main()
