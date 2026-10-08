"""Private stdlib bootstrap. Executed as a script, before importing ncarnate."""
import json
import os
import sys


def main():
    request = json.loads(sys.stdin.buffer.readline(1024 * 1024))
    if sys.platform == "linux":
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (request["memory_bytes"], request["memory_bytes"]))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    elif sys.platform != "win32":
        raise RuntimeError("unsupported bounded-worker platform")
    sys.path[:] = [sys.argv[1], *request["import_paths"]]
    from ncarnate.core import Plan
    from ncarnate.formats import FileFormat
    from ncarnate.prepared import PreparedPlan, execute_prepared
    from ncarnate.result import EncodingOptions
    from ncarnate.streaming import array_budget
    plan = Plan(request["source"], request["destination"], FileFormat[request["format"]],
                request["operation"], EncodingOptions(**request["options"]))
    prepared = PreparedPlan(plan, request["source_size"], request["source_sha256"])
    with array_budget(request["array_bytes"]):
        result = execute_prepared(prepared)
    resources = {"limit_bytes": request["memory_bytes"], "array_budget_bytes": request["array_bytes"],
                 "backend": "windows-job-committed-memory" if sys.platform == "win32" else "linux-rlimit-address-space",
                 "scope": "isolated serial conversion worker; excludes calling process"}
    if sys.platform == "linux":
        resources["peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    with open(request["report"], "x", encoding="utf-8") as stream:
        json.dump({"result": result, "resources": resources}, stream, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())


if __name__ == "__main__":
    main()
