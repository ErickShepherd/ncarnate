"""Isolated conversion with an OS-enforced worker memory ceiling.

Windows caps committed memory for the entire Job Object; Linux caps the serial
worker's address space. Neither metric is RSS, and caller memory is not included.
The worker writes only a private staging directory. Only its successful parent
can publish the verified output. No in-place conversions or worker pools.
"""
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from ncarnate.errors import NcarnateError
from ncarnate.hashing import sha256_of_file
from ncarnate.handoff import validate_handoff
from ncarnate.prepared import _check_source, _validate_batch


class _WindowsJob:
    """A private job; closing it terminates all associated processes."""
    def __init__(self, limit):
        import ctypes as c
        from ctypes import wintypes as w
        class Basic(c.Structure):
            _fields_ = [("PerProcessUserTimeLimit", c.c_int64), ("PerJobUserTimeLimit", c.c_int64),
                        ("LimitFlags", w.DWORD), ("MinimumWorkingSetSize", c.c_size_t),
                        ("MaximumWorkingSetSize", c.c_size_t), ("ActiveProcessLimit", w.DWORD),
                        ("Affinity", c.c_size_t), ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]
        class IO(c.Structure):
            _fields_ = [(name, c.c_uint64) for name in ("ReadOperationCount", "WriteOperationCount",
                         "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
        class Extended(c.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IO),
                        ("ProcessMemoryLimit", c.c_size_t), ("JobMemoryLimit", c.c_size_t),
                        ("PeakProcessMemoryUsed", c.c_size_t), ("PeakJobMemoryUsed", c.c_size_t)]
        self.c, self.info_type = c, Extended
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        for name, args, result in (
            ("CreateJobObjectW", [c.c_void_p, w.LPCWSTR], w.HANDLE),
            ("SetInformationJobObject", [w.HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL),
            ("QueryInformationJobObject", [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p], w.BOOL),
            ("AssignProcessToJobObject", [w.HANDLE, w.HANDLE], w.BOOL),
            ("TerminateJobObject", [w.HANDLE, w.UINT], w.BOOL),
            ("CloseHandle", [w.HANDLE], w.BOOL),
        ):
            fn = getattr(self.kernel, name)
            fn.argtypes, fn.restype = args, result
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise c.WinError(c.get_last_error())
        info = Extended()
        # JOB_MEMORY | KILL_ON_JOB_CLOSE | DIE_ON_UNHANDLED_EXCEPTION
        info.BasicLimitInformation.LimitFlags = 0x200 | 0x2000 | 0x400
        info.JobMemoryLimit = limit
        if not self.kernel.SetInformationJobObject(self.handle, 9, c.byref(info), c.sizeof(info)):
            error = c.WinError(c.get_last_error())
            self.close()
            raise error

    def assign(self, process):
        if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise self.c.WinError(self.c.get_last_error())

    def peak(self):
        info = self.info_type()
        if not self.kernel.QueryInformationJobObject(self.handle, 9, self.c.byref(info), self.c.sizeof(info), None):
            raise self.c.WinError(self.c.get_last_error())
        return info.PeakJobMemoryUsed

    def close(self):
        if self.handle:
            # A Windows venv launcher can own a second Python process. Wait for
            # the entire job, not just Popen's launcher, before deleting logs.
            self.kernel.TerminateJobObject(self.handle, 1)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                accounting = self.c.create_string_buffer(48)
                if not self.kernel.QueryInformationJobObject(self.handle, 1, accounting, 48, None):
                    break
                if self.c.c_uint32.from_buffer(accounting, 40).value == 0:
                    break
                time.sleep(0.01)
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _run_worker(command, request, *, limit, timeout, directory):
    """Start behind a handshake so the limit precedes all science imports."""
    job = _WindowsJob(limit) if sys.platform == "win32" else None
    environment = dict(os.environ, OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
    process = None
    try:
        with open(Path(directory) / "worker.log", "wb") as log:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=log, stderr=log,
                                       env=environment, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                       start_new_session=(os.name == "posix"))
            if job is not None:
                job.assign(process)
            try:
                process.communicate(json.dumps(request).encode("utf-8") + b"\n", timeout=timeout)
            except subprocess.TimeoutExpired as error:
                raise NcarnateError("bounded conversion exceeded its time limit; output not published", code="WORKER_TIMEOUT") from error
        return process.returncode, job.peak() if job is not None else None
    finally:
        if job is not None:
            job.close()
        if process is not None and process.poll() is None:
            if os.name == "posix":
                import signal
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait()


def execute_bounded(item, *, memory_bytes=512 * 1024 * 1024,
                    array_bytes=16 * 1024 * 1024, timeout_seconds=600):
    """Execute a PreparedPlan with a hard ceiling and return record + resources.

Returns {"result": handoff, "resources": ...}. An existing destination is always
refused. Resume/journals remain in the prepared API; this serial primitive never
overwrites a checkpoint. Unsupported platforms fail before work. OS enforcement
is mandatory: there is no fallback to monitoring or an unenforced conversion.
"""
    if sys.platform not in ("win32", "linux"):
        raise NcarnateError("hard worker memory limits are qualified only on Windows and Linux", code="MEMORY_LIMIT_UNAVAILABLE")
    if (type(memory_bytes) is not int or memory_bytes < 64 * 1024 * 1024
            or type(array_bytes) is not int or not 1024 <= array_bytes <= memory_bytes // 4
            or type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 86400):
        raise ValueError("use at least 64 MiB worker memory, an array budget up to one quarter, and a positive bounded timeout")
    if sys.platform == "linux":
        import resource
        _, hard_limit = resource.getrlimit(resource.RLIMIT_AS)
        if hard_limit != resource.RLIM_INFINITY and memory_bytes > hard_limit:
            raise NcarnateError("requested worker memory exceeds the inherited hard address-space limit", code="MEMORY_LIMIT_UNAVAILABLE")
    _validate_batch((item,), allow_existing=False)
    destination = Path(item.plan.destination)
    staging = Path(tempfile.mkdtemp(prefix=".ncarnate-bounded-", dir=destination.parent))
    published = False
    try:
        request = {"source": item.plan.source, "destination": str(staging / "output.nc"),
                   "source_size": item.source_size, "source_sha256": item.source_sha256,
                   "format": item.plan.detected_format.name, "operation": item.plan.operation,
                   "options": item.plan.options.to_record(), "memory_bytes": memory_bytes,
                   "array_bytes": array_bytes, "report": str(staging / "result.json"),
                   "import_paths": [os.path.abspath(path) for path in sys.path]}
        # The bootstrap is stdlib-only and sets Linux's limit before importing
        # ncarnate (whose package imports native scientific libraries).
        bootstrap = Path(__file__).with_name("_bounded_worker.py")
        try:
            # Bypass the Windows venv launcher: it can spawn a child before
            # assignment to our Job Object, leaving that child outside the cap.
            # Disable site hooks until after the limit; restore the parent's
            # already initialized import paths inside the stdlib bootstrap.
            executable = getattr(sys, "_base_executable", sys.executable)
            returncode, peak_commit = _run_worker([executable, "-I", "-S", str(bootstrap), str(Path(__file__).parent.parent)],
                                                  request, limit=memory_bytes, timeout=timeout_seconds, directory=staging)
        except OSError as error:
            raise NcarnateError(f"could not enforce worker memory limit: {error}", code="MEMORY_LIMIT_UNAVAILABLE") from error
        report_path = staging / "result.json"
        if returncode != 0 or not report_path.is_file():
            # Do not mislabel every native crash as OOM. Logs are diagnostic only.
            detail = (staging / "worker.log").read_bytes()[-2048:].decode("utf-8", "replace")
            raise NcarnateError(f"bounded worker failed (exit {returncode}); no output published. {detail}", code="BOUNDED_WORKER_FAILED")
        if report_path.stat().st_size > 32 * 1024 * 1024:
            raise NcarnateError("worker report exceeds 32 MiB", code="BOUNDED_WORKER_FAILED")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        record = report["result"]
        validate_handoff(record)
        output = staging / "output.nc"
        if (output.is_symlink() or not output.is_file()
                or output.stat().st_size != record["destination"]["size_bytes"]
                or sha256_of_file(str(output)) != record["destination"]["sha256"]):
            raise NcarnateError("bounded worker output identity mismatch", code="BOUNDED_WORKER_FAILED")
        _check_source(item)
        _validate_batch((item,), allow_existing=False, check_source=False)
        record["destination"]["path"] = str(destination)
        # Same-filesystem exclusive publication: unlike replace(), link() cannot
        # clobber a destination that appeared after preflight.
        try:
            os.link(output, destination)
        except FileExistsError as error:
            raise NcarnateError("destination appeared during conversion; no output replaced", code="DESTINATION_COLLISION") from error
        except OSError as error:
            raise NcarnateError(f"exclusive output publication failed: {error}", code="OUTPUT_PUBLISH_FAILED") from error
        published = True
        report["resources"]["peak_job_committed_bytes"] = peak_commit
        return report
    finally:
        active_error = sys.exc_info()[1]
        if staging.resolve().parent != destination.parent.resolve() or not staging.name.startswith(".ncarnate-bounded-"):
            raise RuntimeError("unexpected staging cleanup path")
        # Windows can release a terminated process's inherited file handles
        # slightly after the Job Object reports zero active processes.
        deadline = time.monotonic() + 5
        while True:
            try:
                shutil.rmtree(staging)
                break
            except OSError as error:
                if isinstance(error, PermissionError) and sys.platform == "win32" and time.monotonic() < deadline:
                    time.sleep(0.05)
                    continue
                warning = {"code": "WORKER_CLEANUP_INCOMPLETE", "message": f"staging directory retained at {staging}: {error}"}
                if published:
                    report.setdefault("warnings", []).append(warning)
                elif active_error is not None:
                    logging.getLogger("ncarnate").warning(warning["message"])
                else:
                    raise
                break
