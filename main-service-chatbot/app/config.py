"""Application configuration settings.

This module defines the `Settings` class using Pydantic's BaseSettings,
which loads configuration from environment variables and a .env file.
It centralizes all application-wide settings, making them easily accessible
and type-safe.
"""
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class Settings(BaseSettings):
    """
    Settings class for loading application configuration from environment variables.

    Environment variables are case-insensitive and loaded from a `.env` file
    (if present) as well as the system environment. This provides a robust
    and flexible way to manage application settings across different environments.
    """
    # --- Application Settings ---
    TENANT_CONTEXT_ENABLED: bool = Field(
        False,
        description="When true, tenant / identity context (entity_id, tenant_id, user_id, user_type) "
                    "is read from the caller and stored on every new chatbot_session row. "
                    "With DISABLE_AUTH=False the values come from the JWT claims embedded at login. "
                    "With DISABLE_AUTH=True (host-app/Module-Federation mode) they are read from the "
                    "request headers X-Entity-Id, X-Tenant-Id, X-User-Id, X-User-Type set by the host. "
                    "When False (the default), the hardcoded demo GUIDs are used and existing behaviour "
                    "is preserved - flip this on once the host app supplies real values.",
    )
    DISABLE_AUTH: bool = Field(
        False,
        description="When true, bypasses JWT validation and permission checks on all endpoints, and every "
                    "caller is folded into one dev@local.dev identity - so every chatbot_session/"
                    "chatbot_history row belongs to that same identity too, not to whoever is actually "
                    "asking. Intentional when this service sits behind a host app (e.g. consumed via "
                    "Module Federation) that already authenticates the user and controls who can reach "
                    "this route at all - this service is then not the access-control boundary. The real "
                    "auth this falls back to when False (dbo.users - see scripts/mssql/02_users.sql) has "
                    "no self-registration endpoint, so create a first account with "
                    "scripts/create_superuser.py before setting this False, or nothing can log in.",
    )
    # Origins are exact matches - CORSMiddleware does not expand a wildcard inside
    # an entry, so "http://*:8000" would match nothing. 3006 is where docker-compose
    # publishes the UI, and it calls this service directly on :8000.
    CORS_LOCAL_ORIGINS: str = Field(
        "http://localhost:3000,http://localhost:3006,http://127.0.0.1:3006,http://localhost:5173",
        description="Comma-separated browser origins always allowed to call this API: the local-dev UIs "
                    "(docker-compose UI, vite dev server). Exact origins, no trailing slash. Clear it in a "
                    "deployment that should allow only CORS_ALLOWED_ORIGINS.",
    )
    # The FE is normally consumed via Module Federation from a host app, so this is
    # whatever origin that host app is actually served from - e.g.
    # https://chatbot.apps.<cluster-domain>. Comma-separated, no trailing slash.
    CORS_ALLOWED_ORIGINS: str | None = Field(
        None, description="Comma-separated extra CORS origins, in addition to CORS_LOCAL_ORIGINS"
    )
    GZIP_MIN_SIZE_BYTES: int = Field(
        102400,
        description="Responses smaller than this are sent uncompressed - GZipMiddleware's own default "
                    "threshold, made overridable. Compressing a small response wastes more CPU than it "
                    "saves in bytes on the wire.",
    )
    # --- Logging ---
    LOG_LEVEL: str = Field(
        "INFO",
        description="Log level of this service's own code (the `app` logger, everything the service logs "
                    "itself). DEBUG | INFO | WARNING | ERROR | CRITICAL, case-insensitive. Raise it to "
                    "WARNING to quiet the per-turn INFO lines; lower it to DEBUG when tracing a problem. "
                    "Does not touch uvicorn's own loggers.",
    )
    LOG_LEVEL_LIBRARIES: str = Field(
        "WARNING",
        description="Log level of everything that is not this service's own code: the root logger and the "
                    "noisy third-party loggers (azure, pymongo.client). DEBUG | INFO | WARNING | ERROR | "
                    "CRITICAL, case-insensitive. Lower it only to diagnose a library; at INFO or below the "
                    "Azure SDK logs every HTTP request.",
    )
    # --- Message queue ---
    # QUEUE_TYPE picks the broker; see services/queue/queue_factory.py.
    QUEUE_TYPE: Literal["redis", "celery"] = Field(
        "redis",
        description="Message queue backend. 'redis': the API process consumes the queue itself, one file at a "
                    "time per replica (kb_worker/worker.py). 'celery': the API only sends tasks and a separate "
                    "worker Deployment runs `celery -A app.celery_app worker`, CELERY_CONCURRENCY files at once.",
    )
    # SecretStr: a REDIS_URL with an embedded password (redis://:pass@host:6379/0)
    # is a real, supported way to set one (see redis_queue_service.py) - plain
    # str would print it in cleartext in the startup config dump below.
    REDIS_URL: SecretStr = Field(..., description="Redis connection URL")
    REDIS_PASSWORD: SecretStr | None = Field(None, description="Redis password (optional)")
    CHATBOT_QUEUE_NAME: str = Field(..., description="Name of the queue for chatbot tasks")
    REDIS_SOCKET_TIMEOUT_MARGIN_SECONDS: float = Field(
        10,
        description="How much longer than one blocking pop (WORKER_BRPOP_TIMEOUT) a Redis socket may sit "
                    "idle before it is treated as dead: the socket timeout is WORKER_BRPOP_TIMEOUT plus "
                    "this. Too small and a healthy idle pop times out as a Redis error; too large and a "
                    "black-holed connection takes that much longer to notice.",
    )
    REDIS_HEALTH_CHECK_INTERVAL_SECONDS: int = Field(
        30,
        gt=0,
        description="How often an idle Redis connection is PINGed to notice that it has died, before the "
                    "next command is sent on it. Shorter finds a dead connection sooner at the cost of "
                    "one extra round trip per interval per idle connection.",
    )

    # --- Database ---
    # DB_TYPE picks the backend; see services/db/database_factory.py. The data layer
    # calls stored procedures, so a backend also needs its own scripts/<type>/*.sql.
    # mssql (what OpenShift and docker-compose run) expects a
    # "Server=host,port;UserID=...;Password=...;Database=..." ADO-style string;
    # app/services/db/schema_migrator.py applies scripts/mssql/*.sql to the target server.
    DB_TYPE: Literal["mssql"] = Field(
        "mssql", description="Relational database backend"
    )
    DB_CONNECTION_STRING: SecretStr
    AUTO_APPLY_SCHEMA: bool = Field(
        True,
        description="At startup, create the database if it is missing and apply any pending "
                    "scripts/mssql/*.sql. Turn off when a DBA owns the schema.",
    )
    DB_TLS_CA_FILE: str | None = Field(
        None,
        description="Path to a CA bundle (PEM) inside the container. With Encrypt=yes in DB_CONNECTION_STRING the "
                    "SQL Server's certificate is checked against it. Without it, Encrypt=yes needs "
                    "TrustServerCertificate=yes (encrypted, certificate not checked). The host name in the "
                    "certificate is never compared.",
    )
    DB_LOGIN_TIMEOUT_SECONDS: int = Field(
        10,
        description="How long an MSSQL connection attempt is allowed before giving up. mssql only - "
                    "used both by the service's own connection pool (mssql_service.py) and by "
                    "services/db/schema_migrator.py's startup schema check. scripts/apply_schema.py runs outside "
                    "this Settings class entirely and keeps its own copy of the same default.",
    )
    MSSQL_POOL_SIZE: int = Field(
        5,
        ge=1,
        description="Connections opened at startup and shared by every request. "
                    "Each stored-procedure call holds one for its duration, "
                    "so this is the number of database calls that can run at once; a call beyond it "
                    "waits for a free connection. Raise it if requests queue on the database.",
    )
    SCHEMA_LOCK_TIMEOUT_SECONDS: int = Field(
        120,
        description="How long a starting instance waits for another instance to finish applying the "
                    "schema (the migration lock) before it gives up and stops. Applies to the startup "
                    "schema step only (AUTO_APPLY_SCHEMA); scripts/apply_schema.py keeps its own copy "
                    "of the same default.",
    )

    # --- File Storage ---
    # 'local' is the OpenShift target: a mounted PVC shared with the compute
    # service. 'azure' is what local development still uses.
    STORAGE_TYPE: Literal["azure", "nextcloud", "local"] = Field("azure", description="File storage service to use")

    # Local / persistent-volume storage (used if STORAGE_TYPE is 'local')
    LOCAL_STORAGE_PATH: str | None = Field(
        None, description="Directory the file store lives in; a mounted PVC on OpenShift"
    )
    LOCAL_STORAGE_PUBLIC_URL: str | None = Field(
        None,
        description="Optional base URL serving LOCAL_STORAGE_PATH over HTTP. Without it "
                    "citations cannot be linkified and stay as plain text.",
    )

    # Azure Blob Storage Settings (used if STORAGE_TYPE is 'azure') ---
    AZURE_STORAGE_ACCOUNT_URL: SecretStr | None = Field(
        None, description="Azure Blob Storage account URL (e.g. https://<account>.blob.core.windows.net)"
    )
    AZURE_STORAGE_ACCOUNT_NAME: SecretStr | None = Field(
        None, description="Azure Blob Storage account name"
    )
    AZURE_STORAGE_ACCOUNT_KEY: SecretStr | None = Field(
        None, description="Azure Blob Storage account key"
    )
    AZURE_STORAGE_CONTAINER_NAME: SecretStr | None = Field(
        None, description="Azure Blob Storage container name"
    )
    AZURE_STORAGE_SAS_EXPIRY_MINUTES: int = Field(
        30, description="Expiry time for generated SAS tokens (in minutes)"
    )

    # Nextcloud Settings (used if STORAGE_TYPE is 'nextcloud')
    NEXTCLOUD_URL: str | None = Field(None, description="Nextcloud instance URL")
    NEXTCLOUD_USERNAME: SecretStr | None = Field(None, description="Nextcloud username")
    NEXTCLOUD_PASSWORD: SecretStr | None = Field(None, description="Nextcloud password")

    # --- File Upload Settings ---
    ALLOWED_UPLOAD_EXTENSIONS: str = Field(
        ".pdf,.docx,.doc,.txt,.xlsx,.png,.jpeg,.jpg,.webp,.pptx,.ppt,.html,.htm,.eml,.msg",
        description="Comma-separated extensions (with the leading dot) POST /files accepts. The parser "
                    "in the compute service rejects anything it cannot handle anyway; checking here "
                    "avoids uploading a blob and queuing a kb_worker job for a file that was never "
                    "going to work.",
    )
    MAX_UPLOAD_SIZE_MB: int = Field(
        50,
        description="Largest single file POST /files accepts, in MB; a larger one is a 413. Each file is "
                    "read fully into memory (UploadFile.read()), so this also bounds memory per upload. "
                    "50 MB is a generous default for policy documents and scanned PDFs; raise it if a "
                    "deployment genuinely uploads larger files.",
    )

    # --- JWT Settings ---
    SECRET_KEY: SecretStr = Field(..., description="Secret key for JWT encoding and decoding.")
    ALGORITHM: str = Field("HS256", description="Algorithm used for JWT signing.")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(30, description="Access token expiration time in minutes.")

    # --- Chat: LLM backend ---
    # LLM_TYPE picks the backend; see services/llm/chat_llm_factory.py. Each vendor
    # is one module next to it - azure_llm.py, litellm_llm.py, ollama_llm.py,
    # aws_llm.py, google_llm.py - exposing build/endpoint/model_name/
    # required_settings, so adding another vendor is a new file, a value here and
    # its credentials, nothing else.
    # The backend is named, never inferred from whichever block of env vars is
    # populated (a half-filled LiteLLM block would build an Azure client while
    # the startup log named the LiteLLM model): an unset LLM_TYPE gets the default
    # below, and if that backend is not configured the error names the setting
    # it wants.
    LLM_TYPE: Literal["litellm", "azure", "ollama", "aws", "google"] = Field(
        "azure",
        description="Chat model backend. One module per value in app/services/ "
                    "behind chat_llm_factory.py.",
    )
    # Used when LLM_TYPE=ollama.
    LLM_API_URL: str | None = Field(None, description="Ollama-style chat completion endpoint URL")
    MODEL_NAME: str | None = Field(None, description="Ollama-style chat model name")
    TEMPERATURE: float = Field(0.1, description="Sampling temperature for chat completions")

    # Used when LLM_TYPE=azure. AZURE_OPENAI_API_VERSION has a default, so it is
    # never one of the settings a missing-credentials error can name.
    AZURE_OPENAI_API_KEY: SecretStr | None = Field(None, description="Azure OpenAI API key")
    AZURE_OPENAI_ENDPOINT: str | None = Field(None, description="Azure OpenAI endpoint")
    AZURE_OPENAI_API_VERSION: str = Field("2024-05-01-preview", description="Azure OpenAI API version")
    AZURE_OPENAI_DEPLOYMENT_NAME: str | None = Field(None, description="Azure OpenAI deployment name")

    # Used when LLM_TYPE=litellm: an OpenAI-compatible proxy in front of whatever
    # model it is configured to route to, so which vendor is behind it is the
    # proxy's business rather than this service's.
    LITELLM_BASE_URL: str | None = Field(None, description="LiteLLM proxy base URL, e.g. http://litellm:4000")
    LITELLM_API_KEY: SecretStr | None = Field(None, description="LiteLLM proxy virtual API key")
    LITELLM_MODEL_NAME: str | None = Field(None, description="Model name/alias as configured on the LiteLLM proxy")

    # Used when LLM_TYPE=aws (Bedrock, through the Converse API). The access key
    # pair is optional and must be given together: leave both unset to use the
    # default AWS credential chain, i.e. an IAM role, which is what a cluster
    # deployment wants. Names match boto3's own, so the same values work if they
    # are exported in the environment instead.
    AWS_REGION: str | None = Field(None, description="AWS region of the Bedrock endpoint, e.g. us-east-1")
    AWS_BEDROCK_MODEL_ID: str | None = Field(
        None, description="Bedrock model or inference-profile ID, e.g. anthropic.claude-3-5-sonnet-20241022-v2:0"
    )
    AWS_ACCESS_KEY_ID: SecretStr | None = Field(None, description="AWS access key ID; unset = default credential chain")
    AWS_SECRET_ACCESS_KEY: SecretStr | None = Field(None, description="AWS secret access key")
    AWS_SESSION_TOKEN: SecretStr | None = Field(None, description="AWS session token, for temporary credentials")

    # Used when LLM_TYPE=google: the Gemini API with a plain API key. Vertex AI
    # (service-account authentication) is a different credential model and would
    # be its own vendor module.
    GOOGLE_API_KEY: SecretStr | None = Field(None, description="Google AI Studio / Gemini API key")
    GOOGLE_MODEL_NAME: str | None = Field(None, description="Gemini model name, e.g. gemini-2.5-flash")

    # --- Chat: which model does what ---
    # A chat turn is several sequential LLM calls: query rewrite, rerank, the
    # answer, and for non-English questions a translation. Only the answer needs
    # the strongest model; the other three are short structured jobs that a lighter,
    # faster one does as well, and they run before and after the answer, so their
    # speed is directly the user's wait.
    #
    # Swapping a model is a change to these settings and nothing else. Names are
    # whatever the configured backend calls a model: a LiteLLM alias, an Azure
    # deployment name, an Ollama model, a Bedrock model ID or a Gemini model. The
    # answer always uses the main model (LITELLM_MODEL_NAME /
    # AZURE_OPENAI_DEPLOYMENT_NAME / MODEL_NAME / AWS_BEDROCK_MODEL_ID /
    # GOOGLE_MODEL_NAME).
    LLM_HELPER_MODEL: str | None = Field(
        None,
        description="Model for the rewrite, rerank and translate calls. Unset = the main model.",
    )
    LLM_FALLBACK_MODEL: str | None = Field(
        None,
        description="A second model, on the same backend, that writes the answer when the main model fails "
                    "(an error or timeout before the first word). Unset = no fallback: the turn fails as before.",
    )
    LLM_REWRITE_MODEL: str | None = Field(
        None, description="Overrides LLM_HELPER_MODEL for the query rewrite/expansion. 'main' = the main model."
    )
    LLM_RERANK_MODEL: str | None = Field(
        None, description="Overrides LLM_HELPER_MODEL for the rerank. 'main' = the main model."
    )
    LLM_TRANSLATE_MODEL: str | None = Field(
        None, description="Overrides LLM_HELPER_MODEL for the translation. 'main' = the main model."
    )
    LLM_INTENT_MODEL: str | None = Field(
        None, description="Overrides LLM_HELPER_MODEL for the intent step. 'main' = the main model."
    )
    LLM_DESCRIBE_MODEL: str | None = Field(
        None, description="Overrides LLM_HELPER_MODEL for writing catalog descriptions. 'main' = the main model."
    )
    LLM_GROUNDING_MODEL: str | None = Field(
        None, description="Overrides LLM_HELPER_MODEL for the grounding check. 'main' = the main model."
    )
    INTENT_ROUTING_ENABLED: bool = Field(
        True,
        description="Decide before searching whether a message needs the knowledge base at all, so a "
                    "greeting or 'what do you have?' is answered in a couple of seconds instead of "
                    "running the whole pipeline. Only a message that is nothing but small talk, or a "
                    "request to list the knowledge base, is diverted; every question still goes to "
                    "the full pipeline. Turn off to always run it.",
    )
    LLM_WARM_CONNECTIONS: int = Field(
        3,
        description="How many connections to the model endpoint are kept open between chat turns, "
                    "through one HTTP client shared by every model. Without this every model call "
                    "after a few idle seconds opens a new connection (a TCP and TLS handshake, "
                    "about 1 s at 400 ms round trip), twice on the way to the first word. It has no "
                    "effect on what is asked or answered. 0 = leave the SDK's own client alone. "
                    "Not used with Ollama.",
    )
    LLM_HTTP_MAX_CONNECTIONS: int = Field(
        100,
        description="Max simultaneous connections on the shared HTTP client behind every model call "
                    "(see LLM_WARM_CONNECTIONS). Passed straight to httpx.Limits.",
    )
    LLM_HTTP_MAX_KEEPALIVE_CONNECTIONS: int = Field(
        20,
        description="Max idle connections the shared HTTP client keeps open rather than closing after "
                    "a request. Passed straight to httpx.Limits.",
    )
    LLM_HTTP_KEEPALIVE_EXPIRY_SECONDS: float = Field(
        300.0,
        description="How long an idle connection on the shared HTTP client is kept before it is "
                    "closed. Passed straight to httpx.Limits.",
    )
    LLM_HTTP_TIMEOUT_SECONDS: float = Field(
        600.0,
        description="Overall timeout for a model call on the shared HTTP client. Generous on purpose: "
                    "a long answer, or a slow backend under load, must not be cut off mid-generation. "
                    "Passed straight to httpx.Timeout.",
    )
    LLM_HTTP_CONNECT_TIMEOUT_SECONDS: float = Field(
        10.0,
        description="How long the shared HTTP client waits to establish the TCP/TLS connection itself, "
                    "separate from LLM_HTTP_TIMEOUT_SECONDS - a slow or unreachable endpoint should "
                    "fail fast rather than wait out the full call timeout.",
    )
    LLM_WARM_PING_INTERVAL_SECONDS: float = Field(
        20.0,
        description="How often the warm connections to the model endpoint are touched while nobody is "
                    "chatting (see LLM_WARM_CONNECTIONS), so the server on the other end does not close "
                    "them for being idle.",
    )
    RERANK_SHORT_IDS: bool = Field(
        False,
        description="Speed-up for the rerank call: the model still reads every passage under its full id "
                    "but writes back only the first 8 characters of each, which is most of what it spends "
                    "its time on (about 0.5 s of a ~1.8 s call, measured on gpt-5.4-mini). The passages it "
                    "chooses shift slightly in the lower ranks compared with full ids (top-3 unchanged; "
                    "top-8 overlap 0.77 against 0.85 for the full-id prompt with itself), so it is off "
                    "until you have judged it on your own questions.",
    )
    KB_OVERVIEW_ENABLED: bool = Field(
        False,
        description="Whether the bot may tell users what the knowledge base holds: list its documents "
                    "for 'what do you have?', mention them in a greeting, and name them when it refuses "
                    "an off-topic question. Off, it says only that it can answer questions from its "
                    "documents. Off, the catalog is kept out of every prompt (the query rewrite "
                    "included, so search does not use the document descriptions) and a reply that "
                    "names a document is replaced in code. The catalog is still built.",
    )
    NO_OVERVIEW_REPLY: str = Field(
        "I couldn’t find relevant information in the knowledge base. Please try rephrasing your question.",
        description="What the user is told when the intent step's reply would name a knowledge-base "
                    "document while KB_OVERVIEW_ENABLED is off: the reply is replaced by this text. It "
                    "reaches the user as the answer, so write it in the language and tone of the bot.",
    )
    KB_CATALOG_CACHE_SECONDS: float = Field(
        60.0, description="How long a replica keeps the knowledge-base catalog before re-reading it."
    )
    LLM_AUX_REASONING_EFFORT: str | None = Field(
        None,
        description="OPTIONAL. 'minimal' | 'low' | 'medium' | 'high'. Sent only on the rewrite, "
                    "rerank and translate calls, never on the answer. Only for reasoning models "
                    "(gpt-5, o-series) used as a helper: they otherwise spend seconds 'thinking' "
                    "per call. Leave unset for any other model: they reject the parameter.",
    )

    LLM_ANSWER_REASONING_EFFORT: str | None = Field(
        None,
        description="OPTIONAL. 'minimal' | 'low' | 'medium' | 'high'. Sent on the answer itself. A "
                    "reasoning model (gpt-5, o-series) otherwise 'thinks' for many seconds before "
                    "the first word; 'minimal' answers from the retrieved passages almost at once. "
                    "Leave unset for any other model: they reject the parameter.",
    )

    # --- Retrieval / Rerank / Context tuning ---
    # Tunable per deployment: retrieval breadth, prompt size and the small
    # language heuristics around them. The callers take every value from here,
    # so these defaults are the only place they live.
    RERANK_TOP_K: int = Field(
        8,
        description="Passages kept after reranking - how much context the final answer prompt is "
                    "actually built from. Retrieval casts wider than this on purpose (see "
                    "REWRITE_MAX_QUERIES); the rerank call is what narrows it back down.",
    )
    USER_CONTEXT_RECENT_QUESTIONS: int = Field(
        0, ge=0, le=20,
        description="How many of the user's most recent questions (across all their conversations) are shown "
                    "to the model as part of who is asking, so an answer can lean on what they have been "
                    "working on. 0, the default, shows none and skips the extra database read. They are "
                    "sent to the model provider like any other part of the prompt, which is why it is opt-in.",
    )
    USER_CONTEXT_QUESTION_CHARS: int = Field(
        200, ge=20,
        description="Each recent question is cut to this many characters (USER_CONTEXT_RECENT_QUESTIONS).",
    )
    QUERY_FUSION_RRF_K: int = Field(
        10, ge=1,
        description="Rank-damping constant of the Reciprocal Rank Fusion that merges the searches of one "
                    "question's rewritten queries (the question as asked plus its alternate wordings): a "
                    "chunk scores 1/(k + rank) in each search that returned it. Smaller means the top of "
                    "each search dominates. Starts at the store's own HYBRID_RRF_K; it has not been tuned "
                    "on labelled queries the way that one was. Has no effect on a question that is "
                    "searched only once.",
    )
    RERANK_MAX_CANDIDATES: int = Field(
        20,
        description="Most passages sent to the rerank call, best fused score first. The rerank prompt "
                    "grows with every one, and the tail of a long candidate list is the least likely to "
                    "make RERANK_TOP_K, so it is not worth the model reading it.",
    )
    RERANK_PASSAGE_CHARS: int = Field(
        600,
        description="How much of each candidate passage's text is shown to the rerank call. The rerank "
                    "prompt only needs enough to judge relevance, not the whole passage - the full text "
                    "still reaches the answering model afterwards.",
    )
    RERANK_ID_CHARS: int = Field(
        8,
        description="With RERANK_SHORT_IDS on, how many characters of a chunk's id the model is asked "
                    "to write back. An id is a 36-character UUID; writing out up to RERANK_TOP_K of "
                    "those is most of what the rerank call spends its time on.",
    )

    # --- Chat: rate limit, evidence gate and trace ---
    CHAT_RATE_LIMIT_PER_MINUTE: int = Field(
        30,
        description="Most chat turns (POST /chat and /chat/stream) one user may start per minute; more is a 429. "
                    "0 turns the limit off. Counted in Redis so it holds across replicas (a fixed one-minute "
                    "window); if Redis is unreachable it is counted in memory per replica instead.",
    )
    NO_EVIDENCE_GATE_ENABLED: bool = Field(
        True,
        description="When the search finds nothing (or nothing at or above MIN_RELEVANCE_SCORE), answer with "
                    "NO_EVIDENCE_REPLY straight away instead of asking the model to write an answer from no "
                    "passages. Saves the generation call and rules out an answer made up from nothing; the "
                    "turn is traced as 'no_evidence'.",
    )
    NO_EVIDENCE_REPLY: str = Field(
        "I couldn’t find enough information in the knowledge base to answer that. Try rephrasing, or ask "
        "about a specific document or topic.",
        description="What the user is told when the evidence gate (NO_EVIDENCE_GATE_ENABLED) stops a turn. "
                    "Written in English; it is translated into the question's language when that is not English.",
    )
    MIN_RELEVANCE_SCORE: float = Field(
        0.0,
        description="Hits the store scores below this are dropped before the fusion and rerank, so a turn whose "
                    "best hit is weak reaches the evidence gate. 0 keeps everything. The score is the store's "
                    "own (hybrid) score, not a 0-1 similarity: read `top_score` in GET /admin/chat-trace/{id} "
                    "for real turns before choosing a value.",
    )
    TRACE_MAX_HITS: int = Field(
        30, ge=1, description="Most retrieved passages recorded in one turn's trace (the best-first ones).",
    )
    TRACE_QUERY_MAX_CHARS: int = Field(
        300, ge=1, description="Longest search query kept in a trace; longer ones are cut.",
    )
    TRACE_ENABLED: bool = Field(
        True, description="Store a per-turn trace (queries, retrieved and reranked passages, outcome, model, "
                          "config version) in dbo.chatbot_trace for GET /admin/chat-trace/{history_id}.",
    )

    # --- Retention of the audit log and the chat trace ---
    AUDIT_RETENTION_DAYS: int = Field(
        365, description="Audit-log rows older than this are deleted at startup and by POST /admin/purge. 0 keeps them forever.",
    )
    TRACE_RETENTION_DAYS: int = Field(
        180, description="Chat-trace rows older than this are deleted at startup and by POST /admin/purge. 0 keeps them forever.",
    )

    # --- Document versions ---
    VERSION_DETECTION_ENABLED: bool = Field(
        True,
        description="Read each question for which versions of a document it wants: 'compare with the previous "
                    "version' and 'what did version 2 say' reach older versions, everything else is answered from the "
                    "latest. Off = every question uses the latest versions only (older ones stay stored).",
    )
    VERSION_OVERFETCH_MULTIPLIER: int = Field(
        2, ge=1,
        description="Searches for a scope narrower than 'every version' ask the store for this many times as many "
                    "hits, then keep the ones in scope: an older version of a passage ranks right beside its "
                    "replacement and would otherwise use up a slot. 1 = no over-fetch.",
    )

    # --- Grounding check ---
    # Whether an answer's citations are actually supported by what they cite, checked once
    # after the answer has already been sent to the browser (app/controllers/chat_grounding.py,
    # called from app/routers/chat.py) - an extra LLM call that can never slow down or fail a
    # chat turn, only add to what is known about it afterwards.
    GROUNDING_CHECK_ENABLED: bool = Field(
        True,
        description="Check each answer's citations against the passages they cite, once it has already "
                    "been sent to the browser, and record the verdict on its chatbot_history row "
                    "(scripts/mssql/07_chatbot_grounding.sql). Falls back to a fast local lexical check "
                    "when the LLM call fails or returns no usable verdict.",
    )
    GROUNDING_PASSAGE_CHARS: int = Field(
        800,
        description="How much of each cited passage's text the grounding check is shown "
                    "(GROUNDING_CHECK_ENABLED). Larger than RERANK_PASSAGE_CHARS: judging whether a "
                    "claim is actually supported needs more of the passage than judging relevance does.",
    )
    REWRITE_MAX_QUERIES: int = Field(
        8,
        description="The hard ceiling on how many searches one chat turn can fan out into, however "
                    "many questions the rewrite step returns. Exists to stop a confused model that "
                    "splits a sentence into fifty 'questions' from firing fifty searches; a question "
                    "the user actually asked is never dropped below this ceiling.",
    )
    REWRITE_EXPANSION_BUDGET: int = Field(
        4,
        description="What the rewrite step aims to fan a query out into: splits plus expansion "
                    "variants combined. A budget, not a guillotine - REWRITE_MAX_QUERIES is the only "
                    "hard limit, and alternates are spent before an original question is ever dropped.",
    )
    REWRITE_HISTORY_TURNS: int = Field(
        6,
        description="How many recent chat messages the query-rewrite step is shown. It needs history "
                    "for one thing - resolving 'it', 'this', 'the previous requirement' - which is "
                    "always the last turn or two; bounded so the rewrite prompt cannot grow with the "
                    "conversation.",
    )
    REWRITE_HISTORY_CHARS: int = Field(
        600,
        description="How much of each of those REWRITE_HISTORY_TURNS messages is kept, characters per "
                    "message.",
    )
    INTENT_HISTORY_TURNS: int = Field(
        4,
        description="How many recent chat messages the intent-routing step (chitchat/kb_overview/"
                    "knowledge classification) is shown. A separate window from REWRITE_HISTORY_TURNS - "
                    "same idea, tuned independently since the intent step needs much less context to "
                    "decide whether a message needs the knowledge base at all.",
    )
    INTENT_HISTORY_CHARS: int = Field(
        400,
        description="How much of each of those INTENT_HISTORY_TURNS messages is kept, characters per "
                    "message.",
    )
    INTENT_MAX_WORDS: int = Field(
        12,
        description="Only a message this short or shorter is even offered to the intent-routing step. "
                    "A longer message is a question with a greeting in front of it, or a request in its "
                    "own right, and goes straight to the full pipeline - one less call, and one less "
                    "way to divert something that deserved an answer.",
    )
    GREETING_MORNING_START_HOUR: int = Field(
        5,
        ge=0,
        le=23,
        description="Local hour (0-23) at which 'Good Morning' starts to be used when the bot answers a "
                    "greeting. The four GREETING_*_START_HOUR values must be in ascending order; Night runs "
                    "from GREETING_NIGHT_START_HOUR past midnight until this hour. Only takes effect when "
                    "the chat request carries the user's local hour.",
    )
    GREETING_AFTERNOON_START_HOUR: int = Field(
        12, ge=0, le=23, description="Local hour (0-23) at which 'Good Afternoon' starts; see GREETING_MORNING_START_HOUR."
    )
    GREETING_EVENING_START_HOUR: int = Field(
        17, ge=0, le=23, description="Local hour (0-23) at which 'Good Evening' starts; see GREETING_MORNING_START_HOUR."
    )
    GREETING_NIGHT_START_HOUR: int = Field(
        21, ge=0, le=23, description="Local hour (0-23) at which 'Good Night' starts; see GREETING_MORNING_START_HOUR."
    )
    KB_CATALOG_RENDER_MAX_CHARS: int = Field(
        3500,
        description="The catalog goes into a prompt on every chat turn; past this many characters it "
                    "is cut down to names only (see KbCatalog.render), so a knowledge base with many "
                    "documents does not blow out every prompt's size.",
    )
    KB_CATALOG_DESCRIBE_CHARS: int = Field(
        6000,
        description="How much of a document's start is read to write its catalog description. A "
                    "regulation's first pages - its contents page and introduction - say what it is.",
    )
    KB_CATALOG_DESCRIPTION_MAX_CHARS: int = Field(
        320,
        description="Longest catalog description kept per document, so one verbose reply from the "
                    "describe model cannot bloat every prompt that reads the catalog.",
    )
    KB_CATALOG_NAME_WIDTH: int = Field(
        60,
        description="Longest a document's name gets in the catalog text before it is cut short at a "
                    "word. Applies to the catalog in the prompts and to the names checked against an "
                    "intent reply, so changing it changes what counts as naming a document.",
    )
    KB_NAME_MENTION_MIN_CHARS: int = Field(
        3,
        description="With the overview off, an intent reply is replaced when it contains a document's "
                    "name. A name shorter than this is ignored by that check, so a two-letter document "
                    "name does not match inside ordinary words.",
    )
    KB_CATALOG_KEY: str = Field(
        "_kb_catalog/catalog.json",
        description="Path of the catalog file inside the file store (STORAGE_TYPE). Change it only to "
                    "keep two deployments that share one store from sharing a catalog.",
    )
    CITATION_LINK_STYLE: str = Field(
        "color:#eed202",
        description="Inline CSS put on every citation link in an answer. The chat UI recognises the "
                    "link by its shape, not its style, so this only changes how the citations look.",
    )
    CITATION_LABEL_NAME_WIDTH: int = Field(
        18,
        description="Longest a document's name gets inside an inline citation label ('[SAMA, Pg: 5]') "
                    "before it is cut short at a word. Narrow on purpose: it sits inside a sentence. "
                    "The full name is still on the link's hover text.",
    )
    CHAT_PAGE_SIZE_DEFAULT: int = Field(
        10, description="Records per page in GET /chat/history and GET /chat/session when the caller gives no page_size."
    )
    CHAT_PAGE_SIZE_MAX: int = Field(
        100, description="Largest page_size GET /chat/history and GET /chat/session accept; a larger one is a 422."
    )
    TRANSLATION_MIN_RATIO: float = Field(
        0.5,
        description="How short a translation may be next to the answer it was made from, as a "
                    "fraction. A real translation carries the whole answer over and lands within a "
                    "fraction of its length in every language here; well under half means the model did "
                    "not translate - it summarised, truncated, or wrote back the schema's own "
                    "placeholder instead of the answer.",
    )
    LANGUAGE_SCRIPT_SHARE: float = Field(
        0.6,
        description="Every language borrows - an Arabic answer still writes 'SAMA' and 'IFRS 9' in "
                    "Latin letters. The script holding at least this share of a text's letters is the "
                    "one it is considered written in; below it, the text is mixed enough not to say.",
    )
    LANGUAGE_QUERY_MIN_LETTERS: int = Field(
        4,
        description="A script is only read off text with enough letters to carry one. A query is "
                    "allowed to be short - 'ما هي ساما؟' is the whole question - so its floor is lower "
                    "than an answer's (see LANGUAGE_ANSWER_MIN_LETTERS).",
    )
    LANGUAGE_ANSWER_MIN_LETTERS: int = Field(
        12,
        description="The same floor as LANGUAGE_QUERY_MIN_LETTERS, but for an answer: a one-word reply "
                    "cannot decide what language a whole response is in.",
    )

    # --- Vector store (Search runs in-process, here, on the chat pod - see
    # services/vector/, services/embedding/, chat_context.fetch_context) ---
    VECTOR_STORE: Literal["mongo"] = Field("mongo", description="Vector database backend")
    MONGO_CONNECTION_STRING: SecretStr | None = Field(None, description="MongoDB connection string")
    MONGO_DATABASE_NAME: str | None = Field(None, description="MongoDB database name")
    MONGODB_COLLECTION_NAME: str = Field("chatbot_policies", description="Collection holding the active indexed chunks")
    MONGODB_DISABLED_COLLECTION_NAME: str = Field("chatbot_policies_disabled", description="Collection holding chunks for DISABLE/INACTIVE files — excluded from search")
    MONGO_SERVER_SELECTION_TIMEOUT_MS: int = Field(
        5000, description="How long the Mongo driver waits for a server to respond before giving up on a connection attempt."
    )
    VECTOR_SEARCH_EXACT: bool = Field(
        False,
        description="False (default): Atlas $vectorSearch runs ANN (approximate, HNSW-graph search) - scales "
                    "better on large collections but can occasionally miss a true nearest neighbour. True: it "
                    "runs ENN (exact, exhaustive search). Has no effect on plain community MongoDB, which "
                    "always falls back to an exact brute-force search anyway.",
    )
    VECTOR_OVERFETCH_MULTIPLIER: int = Field(
        10,
        description="ANN (approximate) vector search asks Atlas to consider this many times `limit` "
                    "candidates before its HNSW graph settles on the closest `limit` - over-fetching so a "
                    "true nearest neighbour is not missed at the margin. Not used with VECTOR_SEARCH_EXACT True.",
    )
    VECTOR_INDEX_NAME: str = Field("vector_index", description="The Atlas $vectorSearch index name on each collection.")
    TEXT_INDEX_NAME: str = Field("text_search_index", description="The $text index name on each collection.")
    LEXICAL_SEARCH: Literal["bm25", "text"] = Field(
        "bm25", description="Lexical half of hybrid search: bm25 (in-process, stemmed) | text (MongoDB $text index)."
    )
    BM25_K1: float = Field(1.5, description="Okapi BM25 term-frequency saturation for the in-process lexical index.")
    BM25_B: float = Field(0.75, description="Okapi BM25 length-normalisation strength for the in-process lexical index.")
    HYBRID_FUSION_DEPTH_MULTIPLIER: int = Field(
        4,
        description="Hybrid search fuses a vector-ranked list and a lexical-ranked list, each fetched deeper "
                    "than the caller's own `limit` (depth = max(limit * this, HYBRID_FUSION_DEPTH_MIN)) - a "
                    "chunk that ranks 15th by meaning and 2nd by keywords only makes it into the fused top 10 "
                    "if both source lists were long enough to contain it.",
    )
    HYBRID_FUSION_DEPTH_MIN: int = Field(40, description="Floor on the fusion depth above; see HYBRID_FUSION_DEPTH_MULTIPLIER.")
    HYBRID_LEXICAL_WEIGHT: float = Field(
        0.5,
        description="Reciprocal Rank Fusion weight of the lexical list relative to the vector list (which "
                    "counts as 1.0). Chosen on a labelled set of 180 queries against this knowledge base.",
    )
    HYBRID_RRF_K: int = Field(
        10, description="Reciprocal Rank Fusion's rank-damping constant: a chunk scores weight/(k + rank) in each list it appears in."
    )
    SEARCH_RESULT_LIMIT: int = Field(10, description="How many hits a Search asks the vector store for.")
    SEARCH_WARMUP_SECONDS: int = Field(
        45,
        description="After this many seconds with no Search, one is run in the background to keep the "
                    "embedding model and the database warm. The first question after a quiet spell measurably "
                    "paid extra for a cold search; a real search resets the clock, so this costs at most one "
                    "search per interval and nothing while the chatbot is in use. 0 turns it off.",
    )
    LEGACY_EMBEDDING_MODEL_ID: str = Field(
        "gte-multilingual-base",
        description="Chunks indexed before 'embeddingModel' was recorded carry no model name; this is the "
                    "model they are assumed to have been made with, for the stale-index check at startup.",
    )

    # --- Embedding (query-time, for Search; document-time embedding stays in
    # parsing-indexing-services-chatbot). EMBEDDING_TYPE=local (a local model,
    # needing torch) is deliberately not supported here - see embedding_factory.py.
    EMBEDDING_TYPE: Literal["litellm", "azure", "remote"] = Field("remote", description="Query-time embedding backend")
    EMBEDDING_BASE_URL: str | None = Field(None, description="Embeddings endpoint base URL (litellm/azure/remote)")
    EMBEDDING_MODEL_NAME: str | None = Field(None, description="Embedding model name / deployment name")
    EMBEDDING_API_KEY: SecretStr | None = Field(None, description="Embeddings endpoint API key")
    EMBEDDING_API_VERSION: str = Field("2024-10-21", description="Only for EMBEDDING_TYPE=azure")
    EMBEDDING_DIMENSION: int = Field(
        768,
        description="The vector store's index is created with this many dimensions - must match whatever "
                    "the configured embedding model actually outputs, or every write and query against the "
                    "vector index fails. Not auto-detected: changing the embedding model requires setting "
                    "this and reindexing, deliberately, rather than silently producing a dimension mismatch.",
    )
    EMBEDDING_BATCH_SIZE: int = Field(64, description="Texts per embeddings request. Endpoints cap the inputs per call.")
    EMBEDDING_SEND_DIMENSIONS: bool = Field(
        False, description="Send EMBEDDING_DIMENSION as the request's `dimensions` field, for a model that can shorten its output."
    )
    EMBEDDING_TIMEOUT: float = Field(60.0, description="How long to wait for one embeddings HTTP call before giving up.")
    EMBEDDING_MAX_RETRIES: int = Field(3, description="How many times to retry a rate limit or a transient server/network error.")
    EMBEDDING_RETRY_BACKOFF_BASE: float = Field(
        2.0, description="Retry n waits this ** (n - 1) seconds (1s, 2s, 4s at 2.0); a larger base backs off harder under a rate limit."
    )
    EMBEDDING_RETRY_STATUS_CODES: str = Field(
        "429,500,502,503,504", description="Comma-separated HTTP statuses that are retried; any other status fails the call at once."
    )

    # --- Compute service (REST): Index, Delete, ParseFile - document ingestion
    # only. Search does not go through this: it runs in-process, above.
    INDEXING_SERVICE_URL: str = Field("http://indexing-rpc-server:8001", description="Base URL of the compute service")
    RPC_TIMEOUT_SECONDS: float = Field(
        60.0,
        description="Deadline on every call to the compute service (ParseFile, Index, Delete). Without one "
                    "a hung compute-plane pod blocks the caller forever - a kb_worker task that never times "
                    "out into a retry. One deadline for all three; ParseFile in particular can run OCR over a "
                    "large scanned PDF and may need more room than this if that turns out to be too tight in practice.",
    )

    # --- KB queue worker ---
    KB_WORKER_ENABLED: bool = Field(
        True, description="Run the chatbot_queue consumer in-process. Ignored with QUEUE_TYPE=celery, where the "
                          "API never consumes.",
    )
    CELERY_CONCURRENCY: int = Field(
        4,
        ge=1,
        description="QUEUE_TYPE=celery: how many files one worker pod ingests at once (threads). Raising it is "
                    "the way to ingest faster; the limits are then the compute service and the embedding "
                    "endpoint, not this pod, which mostly waits on them.",
    )
    CELERY_VISIBILITY_MARGIN_SECONDS: int = Field(
        300,
        ge=0,
        description="QUEUE_TYPE=celery: how long past WORKER_LEASE_SECONDS the broker waits before it hands an "
                    "unacknowledged task to another worker. Redis has no per-task ack timeout, only this one: too "
                    "short and a slow ingest is delivered a second time while the first is still running.",
    )
    CELERY_QUEUE_NAME: str | None = Field(
        None, description="QUEUE_TYPE=celery: the Redis list tasks go on. Unset = CHATBOT_QUEUE_NAME + '_celery'.",
    )
    CELERY_CATALOG_REFRESH_DELAY_SECONDS: float = Field(
        30.0,
        description="QUEUE_TYPE=celery: after a file is ingested the knowledge-base catalog is refreshed once, "
                    "this many seconds later, however many files finish in between - so a bulk upload costs "
                    "one refresh (one description call per new document), not one per file.",
    )
    CHATBOT_QUEUE_DLQ_NAME: str | None = Field("chatbot_queue_dlq", description="Dead-letter queue name")
    CHATBOT_QUEUE_MAX_RETRIES: int = Field(3, description="Retries before a task is dead-lettered")
    WORKER_BRPOP_TIMEOUT: int = Field(5, description="Blocking pop timeout, seconds")
    WORKER_ERROR_RETRY_DELAY: int = Field(5, description="Delay after a worker error, seconds")
    WORKER_LEASE_SECONDS: int = Field(
        900,
        gt=0,
        description="How long a worker may hold a task before the queue treats it as lost. A task taken "
                    "off the queue is leased for this long; if the worker dies (OOMKill, rollout, node loss) "
                    "the lease runs out and any replica's worker puts the task back on the queue, counting one "
                    "retry (CHATBOT_QUEUE_MAX_RETRIES) and dead-lettering it when they are spent. Leases are not "
                    "renewed, so this must comfortably exceed the longest legitimate parse + index of one file "
                    "(two RPC_TIMEOUT_SECONDS calls plus the DB writes and catalog refresh): too short and a "
                    "healthy but slow worker has its task taken and run a second time; too long and a lost task "
                    "waits that long before it is recovered.",
    )
    WORKER_REAP_INTERVAL_SECONDS: int = Field(
        60,
        gt=0,
        description="How often each worker looks for expired task leases (see WORKER_LEASE_SECONDS), and "
                    "once when it starts. The check is one small sorted-set read, so a shorter interval only "
                    "shortens how long a lost task sits after its lease runs out; it never speeds up the lease itself.",
    )
    WORKER_ERROR_MESSAGE_MAX_CHARS: int = Field(
        1000,
        description="How much of a permanently-failed kb_worker task's error message is stored on the "
                    "file record (processing_error). The full message is still logged; this only bounds "
                    "what is written to the database column.",
    )
    INGEST_RECOVERY_STUCK_SECONDS: int = Field(
        1800,
        gt=0,
        description="A file in 'pending' or 'processing' for longer than this many seconds is considered "
                    "stuck and is requeued by the startup sweep and the periodic watchdog. Should be well "
                    "above WORKER_LEASE_SECONDS (default 900) so a legitimately slow ingest is not "
                    "interrupted.",
    )
    INGEST_RECOVERY_INTERVAL_SECONDS: int = Field(
        300,
        gt=0,
        description="How often (seconds) the background ingest-recovery task checks for stuck files. "
                    "Set to 0 to disable the periodic check (the startup sweep still runs).",
    )
    # DATABASE_SERVICE_URL is deliberately NOT a field here. docker-compose's
    # shared infra-env sets it to http://main-service:8000, which would point
    # this service's error-log HTTP posting at itself, at a path it does not
    # serve. getattr(settings, 'DATABASE_SERVICE_URL', None) returns None
    # without the field, which is what keeps that off.

    @field_validator("LOG_LEVEL", "LOG_LEVEL_LIBRARIES")
    @classmethod
    def _log_level(cls, value: str) -> str:
        level = value.strip().upper()
        if level not in LOG_LEVELS:
            raise ValueError(f"must be one of {', '.join(LOG_LEVELS)} (got {value!r})")
        return level

    @model_validator(mode="after")
    def _greeting_hours_ascend(self) -> "Settings":
        hours = (
            self.GREETING_MORNING_START_HOUR, self.GREETING_AFTERNOON_START_HOUR,
            self.GREETING_EVENING_START_HOUR, self.GREETING_NIGHT_START_HOUR,
        )
        if list(hours) != sorted(set(hours)):
            raise ValueError(
                "GREETING_MORNING/AFTERNOON/EVENING/NIGHT_START_HOUR must be strictly ascending "
                f"(got {', '.join(map(str, hours))})"
            )
        return self

    model_config = SettingsConfigDict(
        # ONE file: the repo-root .env, for every service and every way of running.
        # (Read relative to the working directory, which is main-service-chatbot/.)
        # Missing file is fine: in a container - Docker or OpenShift - the values
        # arrive as real environment variables and nothing is read from disk, and a
        # real environment variable always beats the file.
        env_file="../.env",
        env_file_encoding="utf-8",  # not the platform default: a Windows locale would garble non-ASCII values
        extra="ignore",  # Ignores any extra environment variables not defined in the model.
        case_sensitive=False, # Treats environment variable names as case-insensitive.
    )


@lru_cache
def get_settings() -> Settings:
    """
    Retrieves the application settings with caching for optimization.

    This function uses `functools.lru_cache` to ensure that the `Settings`
    instance is created only once, improving performance by avoiding redundant
    environment variable loading.

    Returns:
        Settings: An instance of the application settings.
    """
    return Settings()
