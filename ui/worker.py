# ruff: noqa: E402
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from promofinder.config import Config
from promofinder.llm import LLMProvider, LLMRouter
from promofinder.pipeline import PromoPipeline
from promofinder.storage import write_json
from ui.batches import BatchProvider, DeferredBatch, prepare_tasks
from ui.providers import DashboardProvider, build_config


def execute(job, keys=None):
    settings = json.loads((job / "settings.json").read_text())
    config = Config.load(ROOT / "config.toml")
    keys = keys or {}
    if not settings["parse_only"]:
        config = build_config(config, settings.get("agents", {}), keys)
    config.pipeline.max_candidates_per_episode = settings["cap"]

    class Router(LLMRouter):
        def prepare(self, stage, tasks):
            if isinstance(self.providers.get(stage), BatchProvider):
                prepare_tasks(self.providers, tasks)

        def call(self, stage, system, user, output, validate=None):
            while (job / "pause").exists():
                write_json(job / "live.json", {"state": "paused", "stage": stage})
                time.sleep(1)
            time.sleep(settings["delay"])
            path = job / "requests" / f"{time.time_ns()}.json"
            record = {
                "stage": stage,
                "system": system,
                "user": user,
                "schema": output.model_json_schema(),
                "state": "running",
            }
            write_json(path, record)
            write_json(job / "live.json", {"state": "running", "stage": stage})
            try:
                result = super().call(stage, system, user, output, validate)
                record.update(state="complete", output=result.model_dump())
                return result
            except DeferredBatch:
                record.update(state="queued_for_batch")
                raise
            except Exception as exc:
                record.update(state="failed", error=str(exc))
                raise
            finally:
                write_json(path, record)

    try:
        providers: dict[str, LLMProvider] = (
            {}
            if settings["parse_only"]
            else {
                stage: (
                    BatchProvider(
                        row["model"],
                        keys[stage],
                        job / "batches" / stage,
                        notify=lambda state, stage=stage: write_json(
                            job / "live.json", {**state, "stage": stage}
                        ),
                    )
                    if row.get("batch")
                    else DashboardProvider(row["provider"], row["model"], keys[stage])
                )
                for stage, row in settings["agents"].items()
            }
        )
        router = Router(config, ROOT / "runs/cache", providers=providers)
        run = PromoPipeline(config, job / "runs", router).run(
            job / "inputs", parse_only=settings["parse_only"]
        )
        write_json(job / "live.json", {"state": "finished", "run": str(run)})
    except Exception as exc:
        write_json(job / "live.json", {"state": "failed", "error": str(exc)})


if __name__ == "__main__":
    # Private pipe from the dashboard, never command-line arguments or files.
    job = Path(sys.argv[1])
    keys = json.loads(sys.stdin.read() or "{}")
    # OS lock releases on exit/crash; reconnecting cannot duplicate an active worker.
    with (job / "worker.lock").open("a+b") as lock:
        lock.write(b"0")
        lock.flush()
        lock.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            sys.exit("This job already has an active worker; reconnect by inspecting its status.")
        execute(job, keys)
