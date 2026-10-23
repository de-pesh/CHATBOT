"""Compute service configuration.

Each piece of infrastructure is selected by a *_TYPE setting with its
credentials beside it, so a backend swap is configuration rather than an edit
to the startup path:

    STORAGE_TYPE    local | azure            -> services/storage/storage_factory.py
    VECTOR_STORE    mongo | qdrant           -> services/vector/vector_store_factory.py
    EMBEDDING_TYPE  local | litellm | azure | remote -> services/embedding/embedding_factory.py
    VISION_LLM_TYPE litellm | azure | foundry | ollama | openai | none
                                             -> parsing/vision/vision_llm_factory.py

STORAGE_TYPE must match main-service's. Only paths cross the REST boundary, so
the two processes have to be looking at the same store - under 'local' that is
the same mounted volume, which on OpenShift means one ReadWriteMany PVC in both
pods.
"""
from functools import lru_cache
from typing import Literal

from app.logic.storage_paths import parse_dir_mode
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- File storage ---
    # 'local' is the OpenShift target: a mounted PVC shared with main-service.
    STORAGE_TYPE: Literal["local", "azure"] = "azure"

    # Used if STORAGE_TYPE is 'local'.
    LOCAL_STORAGE_PATH: str | None = None
    # Permission bits, as an octal string ("770", "0775"; 000-777), for the
    # directories the store creates under LOCAL_STORAGE_PATH when a file is
    # written. The default is group-writable because on OpenShift the two pods run
    # as different arbitrary UIDs that share only group 0, so a directory one pod
    # creates has to be writable by the group for the other to add files to it
    # (and for the volume's fsGroup to be useful). Applied with an explicit chmod
    # to every level the store creates, so the process umask (usually 022, which
    # would turn 770 into 750) does not narrow it. Tighten it (750) only if a
    # single UID does all the writing. The root itself is not covered: it is
    # created, if missing, with the default permissions at startup. No effect
    # under STORAGE_TYPE=azure.
    LOCAL_STORAGE_DIR_MODE: str = "770"

    # Used if STORAGE_TYPE is 'azure'. Optional so the service starts without
    # Azure credentials once it is running on a persistent volume.
    BLOB_CONNECTION_STRING: SecretStr | None = None
    BLOB_CONTAINER_NAME: str | None = None

    # --- Vector store ---
    VECTOR_STORE: Literal["mongo", "qdrant"] = "mongo"
    MONGO_CONNECTION_STRING: SecretStr | None = None
    MONGO_DATABASE_NAME: str | None = None
    # Fallback collection. Every RPC carries its own collection_name; this is
    # only the default a store is constructed with.
    VECTOR_COLLECTION_NAME: str = "chatbot_policies"

    # False (default): Atlas $vectorSearch runs ANN (approximate, HNSW-graph
    # search) - scales better on large collections but can occasionally miss a
    # true nearest neighbour. True: it runs ENN (exact, exhaustive search) -
    # scans every vector behind the index, so results are always exactly
    # correct, at the cost of not scaling as well. For KB/policy-sized
    # collections (thousands-tens of thousands of chunks, not billions), exact
    # search is often the safer default. Has no effect on plain community
    # MongoDB, which always falls back to an exact brute-force search anyway.
    VECTOR_SEARCH_EXACT: bool = False
    # Lexical half of hybrid search: bm25 (in-process, stemmed) | text (MongoDB $text index).
    LEXICAL_SEARCH: Literal["bm25", "text"] = "bm25"
    # After this many seconds with no Search, one is run in the background to keep
    # the embedding model and the database warm. The first question after a quiet
    # spell measurably paid 2-3 seconds extra for a cold search; a real search
    # resets the clock, so this costs at most one search per interval and nothing
    # while the chatbot is in use. 0 turns it off.
    SEARCH_WARMUP_SECONDS: int = 45

    # --- Embedding ---
    # local:   sentence-transformers, loaded from EMBEDDING_MODEL_PATH (defaults
    #          to the gte-multilingual-base copy baked into the image at build).
    # litellm: an embeddings model behind a LiteLLM proxy - EMBEDDING_BASE_URL is
    #          the proxy, EMBEDDING_MODEL_NAME its alias, EMBEDDING_API_KEY the
    #          virtual key. The proxy is what reaches Azure/Bedrock/Vertex/etc.
    # azure:   an Azure OpenAI / Foundry embeddings deployment directly -
    #          EMBEDDING_BASE_URL is the resource endpoint, EMBEDDING_MODEL_NAME
    #          the deployment name, EMBEDDING_API_KEY the resource key.
    # remote:  any other OpenAI-compatible /embeddings endpoint (a real Ollama
    #          server's /v1, a self-hosted server). Same client as litellm.
    # Every choice except local is a config change, not a code change, and can
    # be built with EMBEDDING_MODE=remote to leave torch and the model out.
    # Defaults to "remote": main-service-chatbot's own copy of this setting
    # (it embeds queries for Search) does not accept "local" at all - it does
    # not ship torch - and gen_env_sample.py requires the two services' shared
    # settings to default to the same value. "local" is still fully supported
    # here; it is just not the shared default any more.
    EMBEDDING_TYPE: Literal["local", "litellm", "azure", "remote"] = "remote"

    # Used when EMBEDDING_TYPE=local.
    EMBEDDING_MODEL_PATH: str | None = None
    # Where sentence-transformers runs the model: "cpu", "cuda", "cuda:1", "mps".
    # Unset lets it pick a GPU if there is one and fall back to the CPU. Set it to
    # "cpu" to keep a shared GPU free for something else, or to pin one card.
    EMBEDDING_DEVICE: str | None = None
    # Whether loading the local model may run the Python code shipped inside the
    # model folder. The default model (gte-multilingual-base) needs it - its
    # architecture is defined there - so False makes it fail to load. Turn it off
    # only when EMBEDDING_MODEL_PATH points at a standard architecture, to stop a
    # swapped-in model directory from executing code in this process.
    EMBEDDING_TRUST_REMOTE_CODE: bool = True

    # Used when EMBEDDING_TYPE=litellm, azure or remote.
    EMBEDDING_BASE_URL: str | None = None
    EMBEDDING_MODEL_NAME: str | None = None
    EMBEDDING_API_KEY: SecretStr | None = None
    # Only for EMBEDDING_TYPE=azure.
    EMBEDDING_API_VERSION: str = "2024-10-21"
    # Texts per request. Endpoints cap the inputs per call (OpenAI: 2048), and a
    # document is hundreds of chunks.
    EMBEDDING_BATCH_SIZE: int = 64
    # Send EMBEDDING_DIMENSION as the request's `dimensions` field, so a model
    # that can shorten its output (OpenAI text-embedding-3-*) matches it.
    EMBEDDING_SEND_DIMENSIONS: bool = False

    # The vector store's index is created with this many dimensions - it must
    # match whatever the configured embedding model actually outputs (768 for
    # gte-multilingual-base, 1536 for OpenAI text-embedding-3-small, etc.), or
    # every write and query against the vector index fails. Not auto-detected:
    # changing the embedding model requires setting this and reindexing,
    # deliberately, rather than silently producing a dimension mismatch.
    EMBEDDING_DIMENSION: int = 768

    # --- Chunking / Retrieval / OCR tuning ---
    # Characters per chunk for plain-text and email formats (no document model).
    # Used by RecursiveCharacterTextSplitter; not used for Docling JSON blobs.
    CHUNK_SIZE: int = 4000
    CHUNK_OVERLAP: int = 200

    # Maximum tokens per chunk for Docling JSON blobs processed by HybridChunker.
    # Token budget for the embedding model: 512 fits comfortably within the 8192
    # context of gte-multilingual-base and keeps chunk granularity fine enough
    # for GRC control-level retrieval. Raise toward 1024 for denser documents
    # where more context per chunk improves answer quality.
    DOCLING_CHUNK_MAX_TOKENS: int = 512

    # Hard timeout for a single Docling conversion (seconds). Docling runs in a
    # subprocess so a hung tesseract or PIL call can actually be killed; the
    # subprocess is SIGKILL'd when this limit is reached. Keep it well below
    # task_timeout(WORKER_LEASE_SECONDS) to leave room for the indexing step.
    DOCLING_TIMEOUT_SECONDS: float = Field(180.0, gt=0)

    # How many hits the Search RPC asks the vector store for.
    SEARCH_RESULT_LIMIT: int = 10

    # ANN (approximate) vector search asks Atlas to consider this many times
    # `limit` candidates before its HNSW graph settles on the closest `limit` -
    # over-fetching so a true nearest neighbour is not missed at the margin.
    # Not used with VECTOR_SEARCH_EXACT True: exact search scores every vector
    # and has no candidate pool to size.
    VECTOR_OVERFETCH_MULTIPLIER: int = 10

    # Hybrid search fuses a vector-ranked list and a lexical-ranked list, each
    # fetched deeper than the caller's own `limit` (depth = max(limit *
    # HYBRID_FUSION_DEPTH_MULTIPLIER, HYBRID_FUSION_DEPTH_MIN)) - a chunk that
    # ranks 15th by meaning and 2nd by keywords only makes it into the fused
    # top 10 if both source lists were long enough to contain it.
    HYBRID_FUSION_DEPTH_MULTIPLIER: int = 4
    HYBRID_FUSION_DEPTH_MIN: int = 40

    # Reciprocal Rank Fusion weights, chosen on a labelled set of 180 queries
    # against this knowledge base (see the README's "Hybrid search" section):
    # the lexical list counts half as much as the vector list, and k is small
    # so the top of each list dominates the merged ranking.
    HYBRID_LEXICAL_WEIGHT: float = 0.5
    HYBRID_RRF_K: int = 10

    # How long the Mongo driver waits for a server to respond before giving up
    # on a connection attempt.
    MONGO_SERVER_SELECTION_TIMEOUT_MS: int = 5000

    # --- Qdrant (VECTOR_STORE=qdrant) ---
    # gRPC or HTTP endpoint, e.g. http://qdrant:6333 or grpcs://host:6334.
    QDRANT_URL: str | None = None
    # API key for Qdrant Cloud or a key-protected self-hosted instance.
    QDRANT_API_KEY: SecretStr | None = None
    # Seconds a single Qdrant request may take before it is abandoned.
    QDRANT_TIMEOUT: float = 30.0

    # Okapi BM25 term-frequency saturation (k1) and length-normalisation
    # strength (b) for the in-process lexical index (logic/lexical.py) - the
    # standard textbook defaults.
    BM25_K1: float = 1.5
    BM25_B: float = 0.75

    # Used when EMBEDDING_TYPE=litellm, azure or remote: how long to wait for
    # one embeddings HTTP call before giving up, and how many times to retry a
    # rate limit or a transient server/network error.
    EMBEDDING_TIMEOUT: float = 60.0
    EMBEDDING_MAX_RETRIES: int = 3
    # Retry n waits EMBEDDING_RETRY_BACKOFF_BASE ** (n - 1) seconds (1s, 2s, 4s at
    # 2.0); a larger base backs off harder under a rate limit. A network error is
    # always retried; an HTTP error only if its status is in the comma-separated
    # EMBEDDING_RETRY_STATUS_CODES (any other status fails the call at once).
    EMBEDDING_RETRY_BACKOFF_BASE: float = 2.0
    EMBEDDING_RETRY_STATUS_CODES: str = "429,500,502,503,504"

    # The long side an image handed to the vision model is capped at - whether
    # it arrived as an upload or was rendered from a scanned PDF page - so
    # neither route can overrun the model's context window.
    VISION_MAX_IMAGE_SIDE: int = 2048
    # The largest share of a multi-page document's pages that may come out empty before parsing is
    # refused as unreliable (a scan the OCR could not read would otherwise be indexed as a few stray
    # pages). 0.5 = more than half blank fails; 1.0 turns the check off.
    MAX_BLANK_PAGE_SHARE: float = 0.5
    # What a scanned PDF page is rasterised at before that cap applies.
    VISION_PAGE_RENDER_DPI: int = 200

    # JPEG re-encode quality for an uploaded image that needs re-encoding
    # (rotated via EXIF metadata, or scaled down to VISION_MAX_IMAGE_SIDE).
    IMAGE_JPEG_QUALITY: int = 92

    # Chunks embedded per request by tools/reindex.py. Overridable per run with
    # that script's own --batch-size flag.
    REINDEX_BATCH_SIZE: int = 32

    # --- Vector index names ---
    # The Atlas $vectorSearch index and the $text index created on each collection.
    # Change only to keep two deployments' indexes apart on a shared collection
    # (an existing index under the old name is then no longer used).
    VECTOR_INDEX_NAME: str = "vector_index"
    TEXT_INDEX_NAME: str = "text_search_index"

    # Chunks indexed before `embeddingModel` was recorded carry no model name. This
    # is the model they are assumed to have been made with - the only one this
    # service shipped with then - for the stale-index check at startup and for
    # tools/reindex.py deciding what still needs re-embedding.
    LEGACY_EMBEDDING_MODEL_ID: str = "gte-multilingual-base"

    # --- REST server ---
    # Python logging level of the server.
    LOG_LEVEL: str = "INFO"
    PORT: int = 8001
    # Address the server binds. "0.0.0.0" is every interface; use "127.0.0.1" to
    # accept only local connections.
    HOST: str = "0.0.0.0"

    # --- Vision model (ParseFile: images and scanned pages only) -----------
    # Everything else extracts without it, so an unconfigured vision backend
    # only breaks those two cases - see api_server.py, which builds whichever
    # client below has its env vars set and leaves it None otherwise, logging
    # a warning rather than failing the whole service to start.
    #
    # VISION_LLM_TYPE picks the backend; see parsing/vision/vision_llm_factory.py. Each
    # one is a module next to it, and the same variable names as
    # main-service-chatbot's chat LLM wherever the backend is the same, so
    # configuring either service's LLM is the same mental model. Only fill in
    # the block for the value set here.
    #   'none'  no vision model in this deployment - scanned PDFs are read by
    #           tesseract alone (when installed) and images fail; the rest of the
    #           service is unaffected. The default, because it is the only value
    #           that needs no credentials: the vision model is opt-in.
    VISION_LLM_TYPE: Literal["litellm", "azure", "foundry", "ollama", "openai", "none"] = "none"

    # Used when VISION_LLM_TYPE=litellm: an OpenAI-compatible proxy in front of
    # whatever vision-capable model it is configured to route to.
    LITELLM_BASE_URL: str | None = None
    LITELLM_API_KEY: SecretStr | None = None
    LITELLM_MODEL_NAME: str | None = None

    # Used when VISION_LLM_TYPE=openai: any OpenAI-compatible /v1 endpoint (an
    # Azure AI Foundry resource's .../openai/v1, OpenAI itself, vLLM) reached
    # directly. Its own variables, not LITELLM_*: the root .env is shared with
    # main-service-chatbot, whose chat model already uses those, usually on a
    # different resource. MODEL is the deployment name on Azure.
    VISION_OPENAI_BASE_URL: str | None = None
    VISION_OPENAI_API_KEY: SecretStr | None = None
    VISION_OPENAI_MODEL: str | None = None

    # Used when VISION_LLM_TYPE=azure. AZURE_OPENAI_API_VERSION has a default,
    # so it is never one of the settings a missing-credentials error can name.
    AZURE_OPENAI_API_KEY: SecretStr | None = None
    AZURE_OPENAI_ENDPOINT: str | None = None
    AZURE_OPENAI_API_VERSION: str = "2024-05-01-preview"
    AZURE_OPENAI_DEPLOYMENT_NAME: str | None = None

    # Used when VISION_LLM_TYPE=foundry - langchain_azure_ai's
    # AzureAIChatCompletionsModel, for a model served from the Azure AI Foundry
    # catalog rather than an Azure OpenAI deployment (e.g. a Llama vision model).
    AZURE_AI_FOUNDRY_ENDPOINT: str | None = None
    AZURE_AI_FOUNDRY_API_KEY: SecretStr | None = None
    AZURE_AI_FOUNDRY_MODEL: str | None = None
    AZURE_AI_FOUNDRY_API_VERSION: str = "2024-05-01-preview"

    # Used when VISION_LLM_TYPE=ollama. Plain Ollama needs no key; a server
    # behind an auth proxy is reached through VISION_LLM_TYPE=litellm instead.
    OLLAMA_BASE_URL: str | None = None
    OLLAMA_MODEL: str = "llama3.2-vision:11b"

    # Shared across whichever of the four backends above is selected.
    VISION_TEMPERATURE: float = 0.1

    # Ollama only. Ollama applies a small default context window (2048 on older
    # servers, 4096 on newer ones) and silently truncates anything longer, which
    # would cut the indexing prompt off part-way once a page image's own tokens
    # are added - the model then answers from whatever survived, with no error.
    # The other three backends take their window from the deployment, so this is
    # not passed to them. Raise it for denser pages; it costs VRAM on the server.
    VISION_NUM_CTX: int = 8192

    # --- Relational database (MSSQL) - used by the celery worker ---
    DB_TYPE: Literal["mssql"] = Field("mssql", description="Relational database backend (worker only)")
    DB_CONNECTION_STRING: SecretStr | None = Field(None, description="ADO-style MSSQL connection string")
    DB_LOGIN_TIMEOUT_SECONDS: int = Field(10, description="MSSQL connection timeout")
    MSSQL_POOL_SIZE: int = Field(5, ge=1, description="MSSQL connection pool size")
    DB_TLS_CA_FILE: str | None = Field(None, description="Path to CA bundle for MSSQL TLS")

    # --- Queue / worker ---
    REDIS_URL: SecretStr | None = Field(None, description="Redis URL for the Celery broker")
    REDIS_PASSWORD: SecretStr | None = Field(None, description="Redis password (optional)")
    CHATBOT_QUEUE_NAME: str = Field("chatbot_queue", description="Base queue name")
    CELERY_QUEUE_NAME: str | None = Field(None, description="Celery queue name (defaults to <CHATBOT_QUEUE_NAME>_celery)")
    CELERY_CONCURRENCY: int = Field(4, ge=1, description="Celery worker concurrency (files processed at once)")
    CELERY_CATALOG_REFRESH_DELAY_SECONDS: float = Field(5.0, description="Debounce delay for catalog refresh notifications")
    CELERY_VISIBILITY_MARGIN_SECONDS: int = Field(60, description="Seconds added to lease for Celery visibility timeout")
    WORKER_LEASE_SECONDS: float = Field(300.0, description="How long a worker may hold a task before it is reclaimed")
    WORKER_ERROR_RETRY_DELAY: int = Field(30, description="Seconds between ingest retries")
    WORKER_ERROR_MESSAGE_MAX_CHARS: int = Field(500, description="Max chars stored from a failure reason")
    CHATBOT_QUEUE_MAX_RETRIES: int = Field(3, description="How many times to retry a failed ingest before dead-lettering")
    RPC_TIMEOUT_SECONDS: float = Field(120.0, description="Timeout for a single parse or index RPC call")

    # --- Catalog refresh callback ---
    # URL of main-service-chatbot. After a successful ingest the worker POSTs to
    # {MAIN_SERVICE_URL}/internal/catalog/refresh so main-service can update
    # knowledge-base document descriptions. Leave blank to skip.
    MAIN_SERVICE_URL: str | None = Field(None, description="Base URL of main-service-chatbot (for catalog refresh)")

    # ONE file: the repo-root .env, shared with main-service-chatbot (read relative
    # to the working directory, which is this service's folder). Absent in a
    # container, where the values are real environment variables instead - and
    # those always win over the file.
    model_config = SettingsConfigDict(env_file="../.env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("LOCAL_STORAGE_DIR_MODE")
    @classmethod
    def _octal_permission(cls, value: str) -> str:
        parse_dir_mode(value)
        return value


@lru_cache
def get_settings() -> Settings:
    """The settings, read from the environment once and cached."""
    return Settings()
