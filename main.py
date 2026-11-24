"""Run the complete SIS2 pipeline:

    API -> Kafka producer -> Kafka topic -> Kafka consumer -> Pandera validation
        -> pandas/NumPy cleaning -> SQLite3 -> EDA -> verification

Usage:
    python main.py                 # run every stage
    python main.py --fresh         # delete old outputs and the database first
    python main.py --from-stage validate   # restart from a later stage
    python main.py --report        # also build report/project_report.pdf at the end

Each stage is a separate Python script run as its own process; its console output
is also saved to logs/<number>_<stage>.log (used as evidence in the report).
"""
import argparse
import os
import shutil
import subprocess
import sys
import time

from src import config

STAGES = [
    ("collect", "src.api_client", "API data collection"),
    ("produce", "src.producer", "Kafka producer"),
    ("consume", "src.consumer", "Kafka consumer"),
    ("validate", "src.validator", "Pandera validation"),
    ("clean", "src.cleaner", "Cleaning and transformation"),
    ("load", "src.loader", "SQLite3 loading (run twice, idempotency test)"),
    ("analyze", "src.analysis", "EDA and visualisation"),
    ("verify", "src.verification", "Pipeline verification"),
]


def fresh_start() -> None:
    for path in (config.DATA_DIR, config.LOGS_DIR, config.FIGURES_DIR):
        if path.exists():
            for item in path.iterdir():
                if item.name == ".gitkeep":
                    continue
                shutil.rmtree(item) if item.is_dir() else item.unlink()
    print("[main] --fresh: old outputs removed", flush=True)


def run_stage(number: int, name: str, module: str, title: str) -> int:
    print(f"\n=== Stage {number}: {title} ({module}) ===", flush=True)
    config.LOGS_DIR.mkdir(exist_ok=True)
    log_path = config.LOGS_DIR / f"{number:02d}_{name}.log"
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    started = time.time()
    with open(log_path, "w", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, "-m", module], cwd=config.PROJECT_ROOT, env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        for line in process.stdout:
            print(line, end="", flush=True)
            log.write(line)
        code = process.wait()
        log.write(f"\n[exit code {code}]\n")
    print(f"=== Stage {number} finished with exit code {code} in {time.time() - started:.1f}s ===", flush=True)
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description="SIS2 air-quality streaming pipeline")
    parser.add_argument("--fresh", action="store_true", help="remove previous outputs (data/, logs/, figures/) first")
    parser.add_argument("--from-stage", choices=[s[0] for s in STAGES], default="collect")
    parser.add_argument("--report", action="store_true", help="build the PDF report after a successful run")
    args = parser.parse_args()

    if args.fresh:
        fresh_start()
    start = [s[0] for s in STAGES].index(args.from_stage)
    for number, (name, module, title) in enumerate(STAGES, start=1):
        if number - 1 < start:
            continue
        if run_stage(number, name, module, title) != 0:
            print(f"\n[main] Pipeline stopped: stage '{name}' failed. See logs/{number:02d}_{name}.log", flush=True)
            return 1
    print("\n[main] Pipeline finished successfully.", flush=True)

    if args.report:
        code = subprocess.call([sys.executable, str(config.REPORT_DIR / "build_report.py")], cwd=config.PROJECT_ROOT)
        return code
    return 0


if __name__ == "__main__":
    sys.exit(main())
