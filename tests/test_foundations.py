from pathlib import Path

import pytest
from langchain_core.embeddings import Embeddings
from langchain_core.messages import AIMessage

from revisiter.config import Settings
from revisiter.ingestion import Chunk, read_project
from revisiter.ollama_client import create_chat_model
from revisiter.workflow import build_question_graph


def test_cloud_requires_key_and_config_hides_it():
    with pytest.raises(ValueError, match="OLLAMA_API_KEY"):
        Settings("cloud", "example-model")
    settings = Settings("cloud", "example-model", "test-only-placeholder")
    assert "test-only-placeholder" not in repr(settings)


def test_local_model_receives_no_cloud_key():
    client = create_chat_model(Settings("local", "example-model", "test-only-placeholder"))
    assert client.base_url == "http://localhost:11434"
    assert client.client_kwargs["headers"] == {}


def test_import_rejects_secrets_and_binary(tmp_path: Path):
    secret = tmp_path / ".env"
    secret.write_text("example", encoding="utf-8")
    with pytest.raises(ValueError, match="Format"):
        read_project([secret])
    binary = tmp_path / "binary.py"
    binary.write_bytes(b"abc\x00def")
    with pytest.raises(ValueError, match="binaire"):
        read_project([binary])


def test_import_preserves_line_numbers_without_running_code(tmp_path: Path):
    source = tmp_path / "demo.py"
    source.write_text("raise RuntimeError('must not run')\n# line two\n# three\n", encoding="utf-8")
    chunks = read_project([source], lines_per_chunk=2)
    assert [(c.start_line, c.end_line) for c in chunks] == [(1, 2), (3, 3)]


def test_import_limits_file_size(tmp_path: Path):
    source = tmp_path / "large.py"
    source.write_bytes(b"x" * 200_001)
    with pytest.raises(ValueError, match="Limite"):
        read_project([source])


class FakeEmbeddings(Embeddings):
    def embed_documents(self, texts):
        return [[1.0, 0.0] if "panier" in text else [0.0, 1.0] for text in texts]

    def embed_query(self, text):
        return [1.0, 0.0]


class FakeModel:
    def invoke(self, messages):
        assert "panier.py:L1-L2" in messages[-1].content
        assert "autre.py" not in messages[-1].content
        return AIMessage(content="Le panier additionne les prix [panier.py:L1-L2].")


def test_graph_retrieves_context_before_answering_without_network():
    graph = build_question_graph([
        Chunk("autre.py", 1, 1, "autre module"),
        Chunk("panier.py", 1, 2, "total du panier"),
    ], FakeEmbeddings(), FakeModel(), top_k=1)
    result = graph.invoke({"question": "Comment le total est-il calculé ?"})
    assert result["sources"] == ["panier.py:L1-L2"]
    assert "additionne" in result["answer"]
