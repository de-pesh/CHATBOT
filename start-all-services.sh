#!/usr/bin/env bash
#
# Brings up the full local CCPM stack: shared infra (a single-node Redis
# cluster and MySQL via docker compose, MongoDB via its own docker-mongo-1
# container), the MySQL schema, every application service, and the frontend.
# Safe to re-run — already-running pieces are detected and skipped, and every
# SQL script guards itself with DROP IF EXISTS.
#
# Usage:
#   ./start-all-services.sh setup    # once: poetry install + npm install
#   ./start-all-services.sh          # start everything (infra, schema, services, UI)
#   ./start-all-services.sh status   # show what's running
#   ./start-all-services.sh stop     # stop everything this script started

set -u

# Wherever this script lives, so it works from any checkout.
BASE="$(cd "$(dirname "$0")" && pwd)"
STATE_DIR="$HOME/.local/ccpm-local"
LOG_DIR="$STATE_DIR/logs"
PID_DIR="$STATE_DIR/pids"
mkdir -p "$LOG_DIR" "$PID_DIR"

COMPOSE_FILE="$BASE/docker-compose.yml"

# --- helpers ------------------------------------------------------------

port_in_use() {
  ss -tln 2>/dev/null | grep -q ":$1 "
}

# Is a process already running whose cwd is $1 AND whose cmdline matches $2?
# (matched by cwd, not just "python" in the cmdline — `poetry run` execs into
# the venv's python, so the command line no longer contains "poetry" by the
# time it's running. But cwd+"python" alone is too loose: IDE tooling, e.g.
# VSCode's Python extension, also runs python processes with cwd set to
# whatever project folder is open, which caused false "already running"
# matches against a dead service. Requiring a piece of the actual command
# (e.g. "app.main:app" or "-m app") rules those out.)
running_in_dir() {
  local target pattern
  target="$(readlink -f "$1")"
  pattern="$2"
  for pid in $(pgrep -f "$pattern" 2>/dev/null); do
    if [ "$(readlink -f "/proc/$pid/cwd" 2>/dev/null)" = "$target" ]; then
      echo "$pid"
      return 0
    fi
  done
  return 1
}

# start_bg <name> <cwd> <command...>
start_bg() {
  local name="$1" cwd="$2"
  shift 2
  local pidfile="$PID_DIR/$name.pid"

  if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    echo "  [$name] already running (pid $(cat "$pidfile"))"
    return 0
  fi

  local cmd_pattern="$*"
  cmd_pattern="${cmd_pattern#poetry run }"
  local existing
  existing="$(running_in_dir "$cwd" "$cmd_pattern")"
  if [ -n "$existing" ]; then
    echo "  [$name] already running (pid $existing, found by cwd)"
    echo "$existing" > "$pidfile"
    return 0
  fi

  echo "  [$name] starting..."
  (
    cd "$cwd" || exit 1
    nohup "$@" > "$LOG_DIR/$name.log" 2>&1 &
    echo $! > "$pidfile"
  )
}

wait_for_port() {
  local name="$1" port="$2" tries="${3:-30}"
  for _ in $(seq 1 "$tries"); do
    port_in_use "$port" && { echo "  [$name] up on :$port"; return 0; }
    sleep 1
  done
  echo "  [$name] WARNING: not listening on :$port after ${tries}s (check $LOG_DIR/$name.log)"
  return 1
}

stop_all() {
  echo "Stopping services started via $PID_DIR ..."
  for pidfile in "$PID_DIR"/*.pid; do
    [ -e "$pidfile" ] || continue
    name="$(basename "$pidfile" .pid)"
    pid="$(cat "$pidfile")"
    if kill -0 "$pid" 2>/dev/null; then
      echo "  [$name] stopping pid $pid"
      # children first: `npm run dev` execs vite, which would otherwise survive
      # and keep holding :3006.
      pkill -P "$pid" 2>/dev/null
      kill "$pid" 2>/dev/null
    fi
    rm -f "$pidfile"
  done
  echo "Note: shared infra (redis, mongo, mysql containers) is left running."
  echo "      Run 'docker compose -f $COMPOSE_FILE down' if you need to stop them."
}

status_all() {
  echo "=== Infra (docker) ==="
  docker compose -f "$COMPOSE_FILE" ps --format '  {{.Service}} ({{.Name}}) -> {{.Status}}'
  port_in_use 7000 && echo "  redis-cluster :7000 -> up" || echo "  redis-cluster :7000 -> DOWN"
  port_in_use 27017 && echo "  mongod :27017 -> up" || echo "  mongod :27017 -> DOWN"
  mysql -h127.0.0.1 -uroot -ppass ccpm -e "select 1;" >/dev/null 2>&1 && echo "  mysql :3306 -> up" || echo "  mysql :3306 -> DOWN"
  echo "=== Services ==="
  for pidfile in "$PID_DIR"/*.pid; do
    [ -e "$pidfile" ] || continue
    name="$(basename "$pidfile" .pid)"
    pid="$(cat "$pidfile")"
    if kill -0 "$pid" 2>/dev/null; then
      echo "  $name -> running (pid $pid)"
    else
      echo "  $name -> DOWN (stale pidfile)"
    fi
  done
}

# --- dispatch -------------------------------------------------------------

check_prereqs() {
  echo "=== Checking prerequisites ==="
  local missing=0

  for tool in docker poetry npm node; do
    if command -v "$tool" >/dev/null 2>&1; then
      echo "  [$tool] ok"
    else
      echo "  [$tool] MISSING - required"
      missing=1
    fi
  done

  # torch has no wheels for the newest Python, so the two model services need 3.12.
  if command -v python3.12 >/dev/null 2>&1; then
    echo "  [python3.12] ok"
  else
    echo "  [python3.12] MISSING - indexing-rpc-server and parsing-rpc-server"
    echo "               need it (torch has no wheels for 3.13+)"
    missing=1
  fi

  # OCR toolchain: only parsing-rpc-server, and only for scanned PDFs / docs.
  local optional_missing=""
  for tool in tesseract gs qpdf unpaper pngquant soffice; do
    command -v "$tool" >/dev/null 2>&1 || optional_missing="$optional_missing $tool"
  done
  if [ -n "$optional_missing" ]; then
    echo "  [optional] missing:$optional_missing"
    echo "             parsing-rpc-server needs these for OCR and .doc conversion;"
    echo "             everything else works without them."
  fi

  if [ "$missing" -ne 0 ]; then
    echo ""
    echo "Install the missing required tools, then re-run: $0 setup"
    return 1
  fi
  return 0
}

setup_all() {
  check_prereqs || return 1

  # torch will not install on 3.13+; pin these two before poetry resolves them.
  if command -v python3.12 >/dev/null 2>&1; then
    for d in indexing-rpc-server parsing-rpc-server; do
      ( cd "$BASE/$d" && poetry env use python3.12 >/dev/null 2>&1 ) \
        && echo "  [$d] using python3.12"
    done
  fi

  echo "=== Installing Python dependencies (poetry) ==="
  for d in main-service chatbot-inferencing-service chatbot-kb-management-service \
           indexing-rpc-server indexing-service llm-service parsing-rpc-server parsing-service; do
    echo "  [$d]"
    ( cd "$BASE/$d" && poetry install ) || echo "  [$d] FAILED - see above"
  done

  echo "=== Installing frontend dependencies (npm) ==="
  # --force: the dependency tree has peer conflicts
  ( cd "$BASE/FE" && npm install --force ) || echo "  [FE] FAILED"

  echo ""
  echo "=== Done. Now run: $0 ==="
}

case "${1:-start}" in
  setup)
    setup_all
    exit 0
    ;;
  stop)
    stop_all
    exit 0
    ;;
  status)
    status_all
    exit 0
    ;;
esac

# --- 1. shared infra --------------------------------------------------

echo "=== Shared infra (docker) ==="

docker compose -f "$COMPOSE_FILE" up -d redis mysql

# MongoDB runs as its own container (docker-mongo-1) rather than a compose
# service - it already owns :27017, so compose could never bind it.
docker start docker-mongo-1 >/dev/null 2>&1 && echo "  [mongo] container started" || true

wait_for_port "redis-cluster" 7000
wait_for_port "mongod" 27017
wait_for_port "mysql" 3306 60

echo "=== MySQL schema ==="
for f in files_table_and_procedures files_knowledge_base_column \
         get_files_by_entity_procedure chatbot_tables_and_procedures \
         kb_evidence_chain_tables_and_procedures versions_table_and_procedures; do
  if docker exec -i ccpm-mysql mysql -uroot -ppass ccpm \
       < "$BASE/main-service/scripts/$f.sql" 2>/dev/null; then
    echo "  [$f] ok"
  else
    echo "  [$f] FAILED"
  fi
done

# --- 2. gRPC / HTTP servers (no inter-service dependencies to wait on) ---

echo "=== Core servers ==="
start_bg "main-service" "$BASE/main-service" \
  poetry run uvicorn app.main:app --reload
start_bg "indexing-rpc-server" "$BASE/indexing-rpc-server" \
  poetry run python -m app
start_bg "parsing-rpc-server" "$BASE/parsing-rpc-server" \
  poetry run python -m app

wait_for_port "main-service" 8000
wait_for_port "indexing-rpc-server" 8001 45
wait_for_port "parsing-rpc-server" 8002 45

# --- 3. services that depend on the above being reachable ---------------

echo "=== Dependent services ==="
start_bg "chatbot-inferencing-service" "$BASE/chatbot-inferencing-service" \
  poetry run uvicorn app.main:app --port 8003
start_bg "chatbot-kb-management-service" "$BASE/chatbot-kb-management-service" \
  poetry run python -m app
start_bg "parsing-service" "$BASE/parsing-service" \
  poetry run python -m app
start_bg "indexing-service" "$BASE/indexing-service" \
  poetry run python -m app
start_bg "llm-service" "$BASE/llm-service" \
  poetry run python -m app

wait_for_port "chatbot-inferencing-service" 8003

echo "=== Frontend ==="
start_bg "FE" "$BASE/FE" npm run dev
wait_for_port "FE" 3006 45

echo ""
echo "=== Done. Logs in $LOG_DIR, PIDs in $PID_DIR ==="
echo "Run '$0 status' to check, '$0 stop' to stop everything."
