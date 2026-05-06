from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.config import get_settings
from app.core.judge_tuning import JudgeFineTuneManager


def main() -> None:
    settings = get_settings()
    manager = JudgeFineTuneManager(settings)
    state = manager.write_training_corpus(count=50)
    print(
        json.dumps(
            {
                "dataset_path": state.get("dataset_path"),
                "preview_path": state.get("preview_path"),
                "example_count": state.get("example_count"),
                "base_model": state.get("base_model"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
