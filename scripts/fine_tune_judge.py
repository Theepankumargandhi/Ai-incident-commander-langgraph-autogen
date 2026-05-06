from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.config import get_settings
from app.core.judge_tuning import JudgeFineTuneManager


async def _run(args) -> None:
    settings = get_settings()
    manager = JudgeFineTuneManager(settings)
    if args.refresh_only:
        state = await manager.refresh_fine_tune_status()
    else:
        state = await manager.start_fine_tune(count=args.count, suffix=args.suffix)
    print(json.dumps(state, indent=2, default=str))


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or refresh the specialized judge fine-tuning job.")
    parser.add_argument("--count", type=int, default=50, help="Number of synthetic training examples to generate.")
    parser.add_argument("--suffix", type=str, default="incident-judge", help="Fine-tuned model suffix.")
    parser.add_argument("--refresh-only", action="store_true", help="Only refresh the status of an existing job.")
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
