"""LocalEmbeddingService with a stand-in for sentence-transformers (the real one is torch, and is not installed for tests)."""
import importlib
import sys
from types import ModuleType

import numpy as np
import pytest


class FakeModel:
    device = "cpu"
    loaded = []
    options = []

    def __init__(self, path, device=None, trust_remote_code=False):
        FakeModel.loaded.append(path)
        FakeModel.options.append((device, trust_remote_code))

    def encode(self, texts, batch_size, show_progress_bar, convert_to_numpy):
        return np.ones((len(texts), 4), dtype=np.float32)


@pytest.fixture
def local(monkeypatch):
    fake = ModuleType("sentence_transformers")
    fake.SentenceTransformer = FakeModel
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake)
    FakeModel.loaded.clear()
    FakeModel.options.clear()
    return importlib.reload(importlib.import_module("app.services.embedding.local_embedding_service"))


def test_the_model_loads_lazily_once_and_a_single_string_gives_one_vector(local, tmp_path):
    service = local.LocalEmbeddingService(str(tmp_path), batch_size=8)
    assert service.model_id == tmp_path.name and FakeModel.loaded == []
    assert service.generate_embeddings("a").shape == (4,)
    assert service.generate_embeddings(["a", "b"]).shape == (2, 4)
    assert FakeModel.loaded == [str(tmp_path)]


def test_no_path_means_the_model_baked_into_the_image(local):
    assert local.LocalEmbeddingService(None, batch_size=8).model_id == "gte-multilingual-base"


def test_a_missing_model_directory_is_a_type_error_naming_the_load_failure(local, tmp_path):
    with pytest.raises(TypeError, match="failed to load"):
        local.LocalEmbeddingService(str(tmp_path / "nope"), batch_size=8).generate_embeddings("a")


def test_bad_input_is_a_value_error(local, tmp_path):
    with pytest.raises(ValueError):
        local.LocalEmbeddingService(str(tmp_path), batch_size=8).generate_embeddings("")


def test_device_and_trust_remote_code_reach_the_model(local, tmp_path):
    local.LocalEmbeddingService(str(tmp_path), batch_size=8, device="cuda:1", trust_remote_code=False).generate_embeddings("a")
    local.LocalEmbeddingService(str(tmp_path), batch_size=8).generate_embeddings("a")
    assert FakeModel.options == [("cuda:1", False), (None, True)]
