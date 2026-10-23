#!/usr/bin/env python3
"""Run the stack against infrastructure installed on this machine - no Docker.

Configuration is one file, `.env` in the repo root: copy `.env.sample` to
`.env` and edit it. Then:

    python local.py install     a .venv per service + pip install -r requirements.txt (+ embedding model)
    python local.py check       is everything named in .env reachable?
    python local.py db          create the database and apply the schema now (optional:
                                main-service does it itself when it starts)
    python local.py run         start main-service (:8000) and the compute service (:8001)
    python local.py ui          start the web UI dev server (:3006)

Standard library only, so it runs before anything is installed.
"""
import argparse
import os
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
MAIN = ROOT / "main-service-chatbot"
COMPUTE = ROOT / "parsing-indexing-service"
UI = ROOT / "FE"
ENV_FILE = ROOT / ".env"
MODEL_DIR = COMPUTE / "hf_models" / "gte-multilingual-base"

MAIN_PORT = 8000
COMPUTE_PORT = 8001


# ---------------------------------------------------------------- .env ------

def load_env() -> dict:
    """The same KEY=VALUE format the services read; quotes and # comments handled."""
    if not ENV_FILE.exists():
        sys.exit(f"No {ENV_FILE.name} yet. Copy .env.sample to .env and edit it.")
    values = {}
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if value[:1] in "\"'" and value[-1:] == value[:1] and len(value) > 1:
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[key.strip()] = value
    return values


# ------------------------------------------------------------ processes -----

def run(cmd: list, cwd: Path) -> None:
    print(f"\n$ {' '.join(map(str, cmd))}   (in {cwd.relative_to(ROOT)})")
    if subprocess.call(cmd, cwd=cwd) != 0:
        sys.exit(f"Failed: {' '.join(map(str, cmd))}")


def venv_python(service: Path) -> str:
    """The interpreter of the service's own `.venv`. Running it directly means
    stopping a service stops the actual process, and nothing has to be activated."""
    exe = service / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not exe.exists():
        sys.exit(f"{service.name} has no .venv yet. Run `python local.py install` first.")
    return str(exe)


def ensure_venv(service: Path) -> str:
    """Creates `<service>/.venv` if it is missing, and returns its interpreter."""
    marker = service / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not marker.exists():
        run([sys.executable, "-m", "venv", ".venv"], service)
    return str(marker)


# ------------------------------------------------------------- install ------

def cmd_install(args) -> None:
    env = load_env()
    local_embeddings = env.get("EMBEDDING_TYPE", "remote").lower() == "local"

    # The requirements files are generated from each service's poetry.lock, so
    # this installs the same versions as the Docker and OpenShift images.
    for service, files in (
        (MAIN, ["requirements.txt"]),
        (COMPUTE, ["requirements.txt"] + (["requirements-local-embeddings.txt"] if local_embeddings else [])),
    ):
        python = ensure_venv(service)
        run([python, "-m", "pip", "install", "--quiet", "--upgrade", "pip"], service)
        run([python, "-m", "pip", "install", *[arg for f in files for arg in ("-r", f)]], service)

    if local_embeddings and not MODEL_DIR.exists():
        print("\nDownloading the embedding model (~600 MB, once) ...")
        code = (
            "from huggingface_hub import snapshot_download as d; "
            f"d('Alibaba-NLP/gte-multilingual-base', local_dir=r'{MODEL_DIR}'); "
            # config.json maps the model's classes to code in this second repo;
            # fetching it now means the first search does not have to.
            "d('Alibaba-NLP/new-impl')"
        )
        run([venv_python(COMPUTE), "-c", code], COMPUTE)
    print("\nInstalled. Next: python local.py check")


# --------------------------------------------------------------- check ------

def reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except OSError:
        return False


def port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def cmd_check(args) -> None:
    env = load_env()
    problems = []

    def report(ok: bool, what: str, fix: str = "") -> None:
        print(f"  {'OK  ' if ok else 'FAIL'} {what}")
        if not ok:
            problems.append(fix or what)
            if fix:
                print(f"         -> {fix}")

    print("Tools")
    report(sys.version_info >= (3, 12), f"Python {sys.version_info.major}.{sys.version_info.minor} (need 3.12+)")
    node = shutil.which("node")
    node_major = 0
    if node:
        version = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()
        node_major = int(version.lstrip("v").split(".")[0] or 0) if version[:1] == "v" else 0
    report(node_major >= 22, f"Node {node_major or 'not found'} (need 22+, for the web UI)",
           "install Node 22 or newer: https://nodejs.org")

    print("Config (.env)")
    # .env.sample carries a block for every chat model, so the ones not selected
    # by LLM_TYPE keep their placeholders and are not a problem.
    llm_type = env.get("LLM_TYPE", "azure").lower()
    unused = tuple(p for p, t in (("AZURE_OPENAI_", "azure"), ("LITELLM_", "litellm")) if llm_type != t)
    unset = sorted(k for k, v in env.items() if "CHANGE_ME" in v and not k.startswith(unused))
    report(not unset, "no CHANGE_ME placeholders left", f"edit these in .env: {', '.join(unset)}")

    db_type = env.get("DB_TYPE", "mssql").lower()
    report(db_type == "mssql", f"DB_TYPE={db_type}", "DB_TYPE must be mssql")

    storage = env.get("STORAGE_TYPE", "azure").lower()
    if storage == "local":
        path = env.get("LOCAL_STORAGE_PATH", "")
        report(bool(path) and Path(path).is_absolute(), f"LOCAL_STORAGE_PATH={path or '(unset)'} is absolute",
               "LOCAL_STORAGE_PATH must be an absolute path - the two services run from different folders")

    llm = env.get("LLM_TYPE", "azure").lower()
    needs = {
        "azure": ["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT_NAME"],
        "litellm": ["LITELLM_BASE_URL", "LITELLM_API_KEY", "LITELLM_MODEL_NAME"],
        "ollama": ["LLM_API_URL", "MODEL_NAME"],
        "aws": ["AWS_REGION", "AWS_BEDROCK_MODEL_ID"],
        "google": ["GOOGLE_API_KEY", "GOOGLE_MODEL_NAME"],
    }.get(llm)
    if needs is None:
        report(False, f"LLM_TYPE={llm}", "LLM_TYPE must be litellm, azure, ollama, aws or google")
    else:
        missing = [k for k in needs if not env.get(k)]
        report(not missing, f"LLM_TYPE={llm} settings present", f"set in .env: {', '.join(missing)}")

    print("Infrastructure")
    redis = urlparse(env.get("REDIS_URL", ""))
    report(reachable(redis.hostname or "localhost", redis.port or 6379),
           f"Redis at {redis.hostname or 'localhost'}:{redis.port or 6379}", "start Redis, or fix REDIS_URL")

    mongo = urlparse(env.get("MONGO_CONNECTION_STRING", ""))
    if mongo.scheme == "mongodb":
        hostport = mongo.netloc.rpartition("@")[2].split(",")[0]
        host, _, port = hostport.partition(":")
        report(reachable(host or "localhost", int(port or 27017)),
               f"MongoDB at {host or 'localhost'}:{port or 27017}", "start MongoDB, or fix MONGO_CONNECTION_STRING")
    else:
        report(False, f"MONGO_CONNECTION_STRING={env.get('MONGO_CONNECTION_STRING') or '(unset)'}",
               "set MONGO_CONNECTION_STRING to a mongodb://host:port URL")

    conn = {}
    for item in env.get("DB_CONNECTION_STRING", "").split(";"):
        k, sep, v = item.partition("=")
        if sep:
            conn[k.strip().lower()] = v.strip()
    server = conn.get("server") or conn.get("data source") or "localhost"
    host, _, port = server.partition(",")
    port = port or conn.get("port") or 1433
    report(reachable(host, int(port)), f"mssql at {host}:{port}",
           "start SQL Server, or fix Server/Port in DB_CONNECTION_STRING")

    print("Ports")
    for name, port in (("main-service", MAIN_PORT), ("compute service", COMPUTE_PORT)):
        report(port_free(port), f"{port} free for {name}", f"something is already listening on {port}")

    if problems:
        sys.exit(f"\n{len(problems)} problem(s) above.")
    print("\nAll good. Next: python local.py run (main-service creates the database and tables on its first start)")


# ------------------------------------------------------------------ db ------

def cmd_db(args) -> None:
    load_env()
    run([venv_python(MAIN), "scripts/apply_schema.py", *args.rest], MAIN)


# ----------------------------------------------------------------- run ------

def _pump(stream, label: str) -> None:
    for line in iter(stream.readline, ""):
        print(f"[{label}] {line}", end="", flush=True)


def _start(label: str, cmd: list, cwd: Path) -> subprocess.Popen:
    proc = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    threading.Thread(target=_pump, args=(proc.stdout, label), daemon=True).start()
    return proc


def cmd_run(args) -> None:
    load_env()
    main_py, compute_py = venv_python(MAIN), venv_python(COMPUTE)
    uvicorn = [main_py, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(MAIN_PORT)]
    if args.reload:
        uvicorn.append("--reload")

    procs = {
        "compute": _start("compute", [compute_py, "-m", "app"], COMPUTE),
        "kb-worker": _start("kb-worker", [compute_py, "-m", "celery", "-A", "app.celery_app", "worker", "--loglevel=info", "-n", "local@%h"], COMPUTE),
        "main": _start("main", uvicorn, MAIN),
    }
    print(
        f"Starting: main-service http://127.0.0.1:{MAIN_PORT}  |  "
        f"Prompts API http://127.0.0.1:{MAIN_PORT}/prompts  |  "
        f"compute REST 127.0.0.1:{COMPUTE_PORT}  |  kb-worker (celery)\n"
        "  (the first start loads the embedding model - give it a minute)\nCtrl+C stops all."
    )
    failed = False
    try:
        while all(p.poll() is None for p in procs.values()):
            threading.Event().wait(1)
        dead = next(name for name, p in procs.items() if p.poll() is not None)
        print(f"\n{dead} exited with code {procs[dead].returncode}; stopping the other.")
        failed = True
    except KeyboardInterrupt:
        print("\nStopping ...")
    finally:
        for p in procs.values():
            if p.poll() is None:
                p.terminate()
        for p in procs.values():
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
    if failed:
        # A service that dies on its own is a failure; only Ctrl+C is a clean stop.
        sys.exit(1)


# ------------------------------------------------------------------ ui ------

def cmd_ui(args) -> None:
    npm = shutil.which("npm")
    if not npm:
        sys.exit("Node/npm is not installed or not on PATH.")
    if not (UI / "node_modules").exists():
        run([npm, "install"], UI)
    run([npm, "run", "dev"], UI)


# ---------------------------------------------------------------- main ------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("install", help="create a .venv per service and pip install its requirements; download the embedding model").set_defaults(fn=cmd_install)
    sub.add_parser("check", help="verify .env and that Redis, MongoDB and the database are reachable").set_defaults(fn=cmd_check)
    db = sub.add_parser("db", help="create the database and apply the schema now (--status, --force); main-service also does this at startup")
    db.add_argument("rest", nargs=argparse.REMAINDER)
    db.set_defaults(fn=cmd_db)
    r = sub.add_parser("run", help="start main-service and the compute service")
    r.add_argument("--reload", action="store_true", help="restart main-service when its code changes")
    r.set_defaults(fn=cmd_run)
    sub.add_parser("ui", help="start the web UI dev server").set_defaults(fn=cmd_ui)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return
    args.fn(args)


if __name__ == "__main__":
    main()
