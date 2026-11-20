import argparse
import json
import logging
from pathlib import Path

from dotenv import load_dotenv

from .config import Config
from .llm import LLMError
from .pipeline import PromoPipeline


def main() -> int:
    parser = argparse.ArgumentParser(description="Sequential screenplay-to-promo pipeline")
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument("--output", type=Path, default=Path("runs"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument(
        "--parse-only", action="store_true", help="Parse locally without any LLM calls"
    )
    parser.add_argument(
        "--limit", type=int, help="Use the first N episodes; marks series results as partial"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    load_dotenv(args.env_file, override=False)
    try:
        config = Config.load(args.config)
        pipeline = PromoPipeline(config, args.output)
        run_dir = pipeline.run(args.data, parse_only=args.parse_only, limit=args.limit)
        print(f"Artifacts: {run_dir.resolve()}")
        status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
        return 1 if status["status"] == "complete_with_errors" else 0
    except (ValueError, OSError, LLMError) as error:
        logging.error("%s", error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
