"""CLI for the fixed 24-hour Wellington temperature experiment."""
import argparse
from pathlib import Path
from .config import load_config
from .data import fetch
from .training import train


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["fetch", "train", "run"])
    parser.add_argument("--data", type=Path, default=Path("data/raw"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/baseline"))
    parser.add_argument("--config", type=Path, help="JSON experiment config; defaults to packaged config.json")
    parser.add_argument("--evaluate", action="store_true", help="Record gate decision after training; promotion remains explicit")
    parser.add_argument("--training-date", help="UTC date for rolling splits; defaults to today UTC")
    args = parser.parse_args()
    config = load_config(args.config, args.training_date)
    # Cache is scoped to config: changed windows cannot reuse an old response.
    if args.evaluate or config.get("rolling_window") or "features" in config:
        import hashlib
        import json
        key = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:16]
        args.data = args.data / key
        args.output = args.output / key
    if args.command in ("fetch", "run"):
        fetch(args.data, config)
    if args.command in ("train", "run"):
        train(args.data, args.output, config)
        if args.evaluate:
            import json
            from .lifecycle import evaluate_candidate
            from .tracking import configure
            version = json.loads((args.output / "tracking.json").read_text())["model_version"]
            print(json.dumps(evaluate_candidate(configure(), version), indent=2))


if __name__ == "__main__":
    main()
