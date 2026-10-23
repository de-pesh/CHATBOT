"""Azure embedding backend: an embeddings deployment on Azure OpenAI / Azure AI Foundry.

Used when EMBEDDING_TYPE=azure. Same wire format as the generic remote backend
(remote_embedding_service.py); Azure differs only in where the request goes
(logic/embeddings.py's azure_embeddings_url) and how the key is sent, and the
key header is all this subclass changes:

    POST {endpoint}/openai/deployments/{deployment}/embeddings?api-version=...
    api-key: <key>

`endpoint` is the resource URL (https://<name>.openai.azure.com or the Foundry
resource's https://<name>.services.ai.azure.com) and `deployment` is the name
you gave the model when you deployed it - not the model's own name. Foundry's
non-OpenAI embedding models (Cohere, ...) use a different route; put those
behind a LiteLLM proxy and use EMBEDDING_TYPE=litellm.
"""

from app.services.embedding.remote_embedding_service import RemoteEmbeddingService


class AzureEmbeddingService(RemoteEmbeddingService):
    """Calls an Azure OpenAI / Foundry embeddings deployment."""

    def _auth_headers(self) -> dict[str, str]:
        return {"api-key": self.api_key}
