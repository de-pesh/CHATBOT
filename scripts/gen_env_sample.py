"""Generates `.env.sample` from the two Settings classes, so it cannot drift from the code.

    python scripts/gen_env_sample.py           # rewrite .env.sample
    python scripts/gen_env_sample.py --check   # exit 1 if .env.sample is out of date

Every setting either service reads appears in the file, with its description and its default,
grouped by topic. A setting whose default is a concrete value is written UNcommented (so `.env`
alone shows what the system is doing); one with no default, or an empty one, is written commented.
The few values in PRESET are deliberate starting points (the chat model, dev credentials).

To add a setting: add a Field to app/config.py of the service that reads it, run this script, and
commit both. Compose/build variables (ports, image tags, registries) are not Settings fields; they
are the DOCKER table below.
"""
import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ("main-service-chatbot", "parsing-indexing-service")
OUTPUT = ROOT / ".env.sample"

# Values that are starting points rather than the code's default (or that have no default).
PRESET = {
    "LLM_TYPE": "azure",
    "AZURE_OPENAI_API_KEY": "CHANGE_ME",
    "AZURE_OPENAI_ENDPOINT": "https://<your-resource>.cognitiveservices.azure.com/",
    "AZURE_OPENAI_API_VERSION": "2024-12-01-preview",
    "AZURE_OPENAI_DEPLOYMENT_NAME": "gpt-5-mini",
    "LITELLM_BASE_URL": "https://<your-resource>.services.ai.azure.com/openai/v1",
    "LITELLM_API_KEY": "CHANGE_ME",
    "LITELLM_MODEL_NAME": "Kimi-K2.6",
    "LLM_AUX_REASONING_EFFORT": "minimal",
    "LLM_ANSWER_REASONING_EFFORT": "minimal",
    "LLM_HELPER_MODEL": "",
    "STORAGE_TYPE": "local",
    "DISABLE_AUTH": "True",
    "SECRET_KEY": "chatbot-local-dev-secret-key",
    "DB_CONNECTION_STRING": "Server=localhost,1433;UserID=sa;Password=CHANGE_ME;Database=chatbot",
    "REDIS_URL": "redis://localhost:6379/0",
    "MONGO_CONNECTION_STRING": "mongodb://localhost:27017",
    "INDEXING_SERVICE_URL": "http://127.0.0.1:8001",
    "LOCAL_STORAGE_PATH": "CHANGE_ME_ABSOLUTE_PATH",
    "CHATBOT_QUEUE_NAME": "chatbot_queue",
    # These settings exist in both services but with different code defaults;
    # PRESET pins the recommended value and silences the conflict warning.
    "QUEUE_TYPE": "celery",
    "GROUNDING_CHECK_ENABLED": False,
    "WORKER_ERROR_RETRY_DELAY": 30,
    "WORKER_LEASE_SECONDS": 900,
    "WORKER_ERROR_MESSAGE_MAX_CHARS": 1000,
    "RPC_TIMEOUT_SECONDS": 120,
    "CELERY_VISIBILITY_MARGIN_SECONDS": 300,
    "CELERY_CATALOG_REFRESH_DELAY_SECONDS": 5,
}

# One-line operator advice that the code's own description does not carry.
NOTES = {
    "LLM_TYPE": "Which chat model answers. Fill in ONLY the matching block below; the rest are ignored.",
    "LLM_AUX_REASONING_EFFORT": "GPT-5-mini (a reasoning model): minimal. Kimi and most others: leave BLANK, they reject the parameter.",
    "LLM_ANSWER_REASONING_EFFORT": "Same rule as LLM_AUX_REASONING_EFFORT.",
    "TEMPERATURE": "Kimi K2.x may accept only one fixed temperature; if chat errors mention 'temperature', set it to 1.",
    "STORAGE_TYPE": "Must be the same value for both services (they share the file store).",
    "DISABLE_AUTH": "True = no login: fine for local use, never for anything shared. The host app handles auth.",
    "LOCAL_STORAGE_PATH": "Native run: an ABSOLUTE folder (created if missing). Docker and OpenShift mount their own at /data/files.",
    "MONGODB_COLLECTION_NAME": "MUST equal VECTOR_COLLECTION_NAME (the compute service's setting) - nothing checks it at startup.",
    "VECTOR_COLLECTION_NAME": "MUST equal MONGODB_COLLECTION_NAME.",
    "EMBEDDING_DIMENSION": "Must equal the vector length the embedding model returns; the compute service refuses to start otherwise.",
    "EMBEDDING_TYPE": "litellm | azure | remote | local. main-service's Search does not support local (no torch there) - only the "
                      "compute service's own indexing-time embedding does; build the image with EMBEDDING_MODE=remote (section 9) "
                      "for anything but local.",
    "CHATBOT_QUEUE_NAME": "Sharing a Redis with a teammate? Give each of you a different name or you will take each other's upload jobs.",
}

# Settings whose code description adds nothing past the name itself ("Redis connection URL" over
# REDIS_URL, say) - a comment repeating the name in longhand is noise, not documentation, so these
# are given none. NOTES above and the enum "One of: ..." line are unaffected: a name in this set can
# still get a comment from either of those, just not from its own description text.
OBVIOUS = frozenset({
    "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_VERSION", "AZURE_OPENAI_DEPLOYMENT_NAME",
    "LITELLM_API_KEY", "LITELLM_MODEL_NAME", "AWS_SECRET_ACCESS_KEY", "TEMPERATURE",
    "STORAGE_TYPE", "AZURE_STORAGE_ACCOUNT_NAME", "AZURE_STORAGE_ACCOUNT_KEY", "AZURE_STORAGE_CONTAINER_NAME",
    "AZURE_STORAGE_SAS_EXPIRY_MINUTES", "NEXTCLOUD_URL", "NEXTCLOUD_USERNAME", "NEXTCLOUD_PASSWORD",
    "SECRET_KEY", "ALGORITHM", "ACCESS_TOKEN_EXPIRE_MINUTES", "DB_TYPE", "QUEUE_TYPE", "REDIS_URL",
    "REDIS_PASSWORD", "CHATBOT_QUEUE_NAME", "CHATBOT_QUEUE_DLQ_NAME", "CHATBOT_QUEUE_MAX_RETRIES",
    "WORKER_BRPOP_TIMEOUT", "WORKER_ERROR_RETRY_DELAY", "MONGODB_COLLECTION_NAME",
})

# (title, intro, [regexes matched against the setting name; first section that matches wins])
SECTIONS = [
    ("1. CHAT MODEL", "Which AI answers the questions, and how it is called.", [
        r"^LLM_TYPE$", r"^AZURE_OPENAI_", r"^LITELLM_", r"^LLM_API_URL$", r"^MODEL_NAME$", r"^AWS_", r"^GOOGLE_",
        r"^TEMPERATURE$", r"^LLM_(AUX|ANSWER)_REASONING", r"^LLM_(HELPER|REWRITE|RERANK|TRANSLATE|INTENT|DESCRIBE)_MODEL$",
        r"^LLM_HTTP_", r"^LLM_WARM_",
    ]),
    ("2. FILE STORAGE", "Where uploaded documents (and their extracted text) are kept.", [
        r"^STORAGE_TYPE$", r"^LOCAL_STORAGE_", r"^AZURE_STORAGE_", r"^BLOB_", r"^NEXTCLOUD_",
    ]),
    ("3. API, LOGIN & LOGGING", "The HTTP service: auth, CORS, upload limits, paging, log levels.", [
        r"^DISABLE_AUTH$", r"^SECRET_KEY$", r"^ALGORITHM$", r"^ACCESS_TOKEN_", r"^TOKEN_", r"^CORS_", r"^GZIP_", r"^ALLOWED_UPLOAD_",
        r"^MAX_UPLOAD_", r"^CHAT_PAGE_", r"^LOG_LEVEL",
    ]),
    ("4. DATABASE, QUEUE & UPLOAD WORKER", "SQL Server, Redis and the background worker that ingests uploads.", [
        r"^DB_", r"^AUTO_APPLY_SCHEMA$", r"^MSSQL_", r"^SCHEMA_", r"^QUEUE_TYPE$", r"^REDIS_", r"^CHATBOT_QUEUE_",
        r"^KB_WORKER_", r"^WORKER_",
    ]),
    ("5. VECTOR STORE, SEARCH & THE COMPUTE SERVICE",
     "MongoDB and hybrid search (main-service runs Search in-process - see its own copy of these "
     "settings), and the REST link to the compute service for document ingestion.", [
        r"^MONGO", r"^VECTOR_", r"^TEXT_INDEX_NAME$", r"^LEXICAL_", r"^HYBRID_", r"^BM25_", r"^SEARCH_", r"^RPC_",
        r"^INDEXING_SERVICE_URL$", r"^PORT$", r"^HOST$", r"^LEGACY_EMBEDDING_",
    ]),
    ("6. EMBEDDINGS", "Turning text into vectors.", [r"^EMBEDDING_", r"^REINDEX_"]),
    ("7. PARSING, CHUNKING & OCR", "Reading documents and splitting them for indexing.", [
        r"^CHUNK_", r"^VISION_", r"^OCR_", r"^IMAGE_", r"^CONVERTER_", r"^LIBREOFFICE_", r"^AZURE_AI_FOUNDRY_", r"^OLLAMA_",
    ]),
    ("8. CHAT PIPELINE: REWRITE, RERANK, CATALOG, LANGUAGE",
     "How a question becomes an answer, and what it knows about who is asking.", [
        r"^INTENT_", r"^REWRITE_", r"^RERANK_", r"^QUERY_FUSION_", r"^KB_", r"^NO_OVERVIEW_", r"^CITATION_",
        r"^TRANSLATION_", r"^LANGUAGE_", r"^GREETING_", r"^USER_CONTEXT_",
    ]),
]

# Compose / build variables. (name, default, description). A blank default is written commented.
DOCKER = [
    ("COMPOSE_PROJECT_NAME", "chatbot", ""),
    ("CONTAINER_PREFIX", "chatbot", "Prefix for container names and the network: <prefix>-redis, <prefix>-main-service, ..."),
    ("MAIN_SERVICE_HOST_PORT", "8000", "Host port for main-service (the browser calls it directly)."),
    ("INDEXING_HOST_PORT", "8001", "Host port for the compute service's REST server."),
    ("FRONTEND_HOST_PORT", "3006", ""),
    ("REDIS_HOST_PORT", "6379", ""),
    ("MONGO_HOST_PORT", "27017", ""),
    ("MSSQL_HOST_PORT", "1433", ""),
    ("REDIS_IMAGE", "redis:7-alpine", ""),
    ("MONGO_IMAGE", "mongo:7", ""),
    ("MSSQL_IMAGE", "mcr.microsoft.com/mssql/server:2022-latest", ""),
    ("MAIN_SERVICE_IMAGE", "chatbot/main-service:local", "Tag given to the built main-service image."),
    ("INDEXING_IMAGE", "chatbot/indexing-rpc-server:local", "Tag given to the built compute image."),
    ("FRONTEND_IMAGE", "chatbot/frontend:local", "Tag given to the built UI image."),
    ("MSSQL_SA_PASSWORD", "Chatbot_Local_1", "The local SQL Server's sa password: 8+ chars, 3 of upper/lower/digit/symbol, no ';'."),
    ("MSSQL_PID", "Developer", "SQL Server edition."),
    ("MSSQL_DATABASE_NAME", "chatbot", "Database the containers use. A native run names its own in DB_CONNECTION_STRING."),
    ("DOCKER_REDIS_URL", "", "Point the containers at an external Redis instead of the redis container."),
    ("DOCKER_DB_CONNECTION_STRING", "", "Point the containers at an external SQL Server."),
    ("DOCKER_MONGO_CONNECTION_STRING", "", "Point the containers at an external MongoDB."),
    ("BASE_REGISTRY", "registry.access.redhat.com", "BUILD: registry the Red Hat base images come from (an internal mirror on a closed network)."),
    ("PIP_INDEX_URL", "", "BUILD: internal PyPI mirror, when there is one."),
    ("PIP_TRUSTED_HOST", "", "BUILD: host to trust for PIP_INDEX_URL when it is plain http."),
    ("NPM_REGISTRY", "", "BUILD: internal npm registry, when there is one."),
    ("EPEL_RPM_URL", "https://dl.fedoraproject.org/pub/epel/epel-release-latest-9.noarch.rpm", "BUILD: where the compute image gets EPEL (an internal mirror on a closed network)."),
    ("EMBEDDING_MODE", "remote", "BUILD: local bakes torch and the embedding model into the compute image; remote (default) leaves them out. Must match EMBEDDING_TYPE - main-service's Search never supports local anyway."),
    ("HF_MODEL_MODE", "download", "BUILD: download fetches the local embedding model at build time; vendored uses vendor/gte-multilingual-base."),
    ("OCR_MODE", "optional", "BUILD: optional skips OCR/LibreOffice packages that are missing from the mirrors instead of failing."),
    ("OCR_PACKAGES", "tesseract tesseract-langpack-eng tesseract-langpack-ara ghostscript qpdf pngquant unpaper libreoffice-headless", "BUILD: system packages for OCR and Office conversion. Add tesseract-langpack-<code> for more OCR languages."),
    ("BUILD_SCRIPT", "build:local", "BUILD: npm script that builds the UI (build:local = standalone, build:qa = with the Module Federation remote)."),
    ("VITE_API_BASE_URL", "", "BUILD: where the BROWSER reaches main-service, baked into the UI. Blank = http://localhost:8000. Rebuild the frontend after changing it."),
]

DUMP = r"""
import json, sys, typing
from pydantic_core import PydanticUndefined
from app.config import Settings
rows = []
for name, f in Settings.model_fields.items():
    ann = f.annotation
    choices = list(typing.get_args(ann)) if typing.get_origin(ann) is typing.Literal else None
    default = None if f.default is PydanticUndefined else f.default
    if hasattr(default, "get_secret_value"):
        default = default.get_secret_value()
    if not isinstance(default, (str, int, float, bool, type(None))):
        default = repr(default)
    rows.append({"name": name, "required": f.is_required(), "default": default,
                 "choices": choices, "description": " ".join((f.description or "").split())})
sys.stdout.write("@@JSON@@" + json.dumps(rows))
"""


def load_fields(service: str) -> list[dict]:
    root = ROOT / service
    python = next((p for p in (root / ".venv/Scripts/python.exe", root / ".venv/bin/python") if p.exists()), Path(sys.executable))
    result = subprocess.run([str(python), "-c", DUMP], cwd=root, env={**__import__("os").environ, "PYTHONPATH": str(root)},
                            capture_output=True, text=True)
    if result.returncode or "@@JSON@@" not in result.stdout:
        sys.exit(f"could not read {service}'s Settings:\n{result.stderr[-1500:]}")
    return json.loads(result.stdout.split("@@JSON@@", 1)[1])


def ascii_text(text: str) -> str:
    table = {"—": "-", "–": "-", "’": "'", "‘": "'", "“": '"', "”": '"', "…": "...", "→": "->"}
    return "".join(table.get(c, c) for c in text).encode("ascii", "replace").decode("ascii")


def render_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    text = "" if value is None else str(value)
    if re.search(r"[\s#'\"\\]", text):
        return f'"{text}"' if "$" not in text and '"' not in text else f"'{text}'"
    return text


def wrap(text: str, prefix: str = "# ") -> list[str]:
    return [prefix + line for line in textwrap.wrap(ascii_text(text), 100)] or [prefix.rstrip()]


def render_setting(field: dict) -> list[str]:
    """The comment (if any) and the NAME=value line for one setting.

    A setting whose description is in OBVIOUS (see below) is given no comment at all: its name
    already says everything the description would. NOTES, a real description and the enum choices
    are otherwise joined into one paragraph and wrapped together, rather than as separate one-line
    fragments - fewer, denser lines instead of three thin ones for the same information.
    """
    name = field["name"]
    parts = []
    if name in NOTES:
        parts.append(NOTES[name])
    if field["description"] and name not in OBVIOUS:
        parts.append(field["description"])
    if field["choices"]:
        parts.append("One of: " + " | ".join(str(c) for c in field["choices"]) + ".")
    lines = wrap(" ".join(parts)) if parts else []
    if name in PRESET:
        value, active = PRESET[name], True
    else:
        value = field["default"]
        active = value not in (None, "") and ascii_text(str(value)) == str(value)
    if not active:
        value = ascii_text(str(value)) if value is not None else value  # the file stays pure ASCII
    lines.append(f"{'' if active else '# '}{name}={render_value(value)}")
    return lines


def place(out: list[str], lines: list[str], prev_had_comment: bool) -> bool:
    """Appends one setting's rendered lines to `out`. A blank line goes above it unless both it and
    the setting before it have no comment - so a run of bare NAME=value lines, with nothing worth
    saying about any of them, stays visually tight instead of each floating in its own paragraph;
    anything that does have a comment keeps its breathing room. Returns whether this one had a
    comment, to check the next one against.
    """
    has_comment = len(lines) > 1
    if has_comment or prev_had_comment:
        out.append("")
    out.extend(lines)
    return has_comment


def build() -> str:
    merged: dict[str, dict] = {}
    for service in SERVICES:
        for field in load_fields(service):
            if field["name"] in merged:
                if field["name"] not in PRESET and merged[field["name"]]["default"] != field["default"]:
                    print(
                        f"WARNING: {field['name']} has different defaults in the two services "
                        f"({merged[field['name']]['default']!r} vs {field['default']!r}); "
                        "add it to PRESET to suppress this warning.",
                        file=sys.stderr,
                    )
                continue
            merged[field["name"]] = field
    placed: set[str] = set()
    out = [
        "# " + "=" * 76,
        "#  THE ONLY CONFIG FILE - change things here and nowhere else.",
        "#",
        "#  GENERATED by scripts/gen_env_sample.py from the Settings classes: every setting either",
        "#  service reads is listed with its default. Edit your own `.env` (copy of this file), not this.",
        "#",
        "#  Start:   cp .env.sample .env      (Windows: copy .env.sample .env)",
        "#           then fill in the CHANGE_ME values.  `.env` is gitignored.",
        "#  Docker:  docker compose up -d     (after any later edit, run it again: 'restart' does",
        "#           NOT re-read this file). Sections 1-8 reach both containers; section 9 is read by",
        "#           compose itself (ports, images, build arguments).",
        "#  Native:  .\\run-local.ps1  or  python local.py install / check / run  (any OS).",
        "#  OpenShift does not read this file: openshift/01-secrets.yaml + 02-configmap.yaml take the",
        "#           same variable names (docs/openshift-release.md).",
        "#",
        "#  A line starting with '#' is a setting left at its built-in default (or unset).",
        "#  Uncommented lines are active: the value shown is the default unless noted.",
        "#",
    ]
    for title, _, _ in SECTIONS:
        out.append("#    " + title)
    out += ["#    9. DOCKER & BUILD (ports, images, registries)", "# " + "=" * 76, ""]
    for title, intro, patterns in SECTIONS:
        names = [n for n in merged if n not in placed and any(re.search(p, n) for p in patterns)]
        names.sort(key=lambda n: min(i for i, p in enumerate(patterns) if re.search(p, n)))
        out += ["", "# " + "=" * 76] + wrap(f"{title} - {intro}", prefix="#  ")
        prev_had_comment = True  # always a blank line between the banner and the first setting
        for n in names:
            prev_had_comment = place(out, render_setting(merged[n]), prev_had_comment)
            placed.add(n)
    leftover = [n for n in merged if n not in placed]
    if leftover:
        out += ["", "# " + "=" * 76, "#  OTHER"]
        prev_had_comment = True
        for n in leftover:
            prev_had_comment = place(out, render_setting(merged[n]), prev_had_comment)
    out += ["", "# " + "=" * 76, "#  9. DOCKER & BUILD - read by docker-compose.yml, not by the services"]
    prev_had_comment = True
    for name, default, description in DOCKER:
        lines = (wrap(description) if description else []) + [f"{'' if default else '# '}{name}={render_value(default)}"]
        prev_had_comment = place(out, lines, prev_had_comment)
    return "\n".join(out) + "\n"


def main() -> None:
    text = build()
    if "--check" in sys.argv:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != text:
            sys.exit(".env.sample is out of date: run python scripts/gen_env_sample.py")
        print(".env.sample is up to date")
        return
    OUTPUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUTPUT} ({len(text.splitlines())} lines)")


if __name__ == "__main__":
    main()
