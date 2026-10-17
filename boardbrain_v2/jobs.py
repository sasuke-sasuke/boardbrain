from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
from .store import uid, now


class Jobs:
    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="boardbrain")
        self.lock = threading.RLock()
        self.items = {}

    def submit(self, case_id, kind, work):
        with self.lock:
            active = [j for j in self.items.values() if j["status"] in ("queued", "running", "stopping")]
            if len(active) >= 12:
                raise ValueError("The queue is full. Wait for an import to finish.")
            if kind == "assistant" and any(j["kind"] == kind for j in active):
                raise ValueError("An assistant request is already running. Wait or stop it first.")
            job_id = uid("J")
            self.items[job_id] = dict(id=job_id, case_id=case_id, kind=kind, status="queued", message="Queued", created=now(), result=None, cancel=threading.Event())
            self.pool.submit(self._run, job_id, work)
            return self.get(job_id)

    def _run(self, job_id, work):
        job = self.items[job_id]
        def progress(message):
            with self.lock:
                job["message"] = message
        with self.lock:
            job["status"] = "running"
        try:
            if job["cancel"].is_set():
                raise InterruptedError()
            result = work(job["cancel"], progress)
            with self.lock:
                job.update(status="cancelled" if job["cancel"].is_set() else "complete", message="Stopped" if job["cancel"].is_set() else "Complete", result=result)
        except InterruptedError:
            with self.lock:
                job.update(status="cancelled", message="Stopped. An in-flight AI call may still be billed.")
        except Exception as exc:
            with self.lock:
                job.update(status="failed", message=str(exc)[:600] or "Operation failed. Your case is still saved.")
        finally:
            with self.lock:
                old = [j for j in self.items.values() if j["status"] not in ("queued", "running", "stopping")]
                for item in old[:-100]:
                    self.items.pop(item["id"], None)

    def get(self, job_id):
        with self.lock:
            if job_id not in self.items:
                raise ValueError("Job not found")
            return {k: v for k, v in self.items[job_id].items() if k != "cancel"}

    def list(self):
        with self.lock:
            return [self.get(k) for k in self.items]

    def cancel(self, job_id):
        with self.lock:
            self.items[job_id]["cancel"].set()
            self.items[job_id].update(status="stopping", message="Stopping after the current operation…")

    def shutdown(self):
        with self.lock:
            for job in self.items.values():
                job["cancel"].set()
        self.pool.shutdown(wait=False, cancel_futures=True)
