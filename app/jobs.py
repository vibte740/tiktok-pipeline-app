import threading
import time
import traceback
import uuid


class Job:
    def __init__(self, name: str):
        self.id = uuid.uuid4().hex[:12]
        self.name = name
        self.status = "queued"
        self.created = time.time()
        self.started = None
        self.finished = None
        self.logs = []
        self.result = None
        self.error = None

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "created": self.created,
            "started": self.started,
            "finished": self.finished,
            "logs": self.logs[-2000:],
            "result": self.result,
            "error": self.error,
            "log_count": len(self.logs),
        }


class JobManager:
    def __init__(self):
        self._jobs = {}
        self._lock = threading.Lock()

    def submit(self, name: str, fn) -> str:
        job = Job(name)
        with self._lock:
            self._jobs[job.id] = job

        def runner():
            job.status = "running"
            job.started = time.time()
            try:
                job.result = fn(self._logger(job)) or {}
                job.status = "done"
            except Exception as e:
                job.status = "error"
                job.error = str(e)
                self._logger(job)(f"\n[ERROR] {e}\n{traceback.format_exc()}")
            finally:
                job.finished = time.time()

        threading.Thread(target=runner, daemon=True).start()
        return job.id

    def _logger(self, job):
        def log(line):
            with self._lock:
                job.logs.append(str(line))
        return log

    def get(self, jid):
        return self._jobs.get(jid)

    def list(self):
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created, reverse=True)