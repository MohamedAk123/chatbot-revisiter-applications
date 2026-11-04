"""Vérifier le fonctionnement des raccourcis de la nouvelle discussion."""

from pathlib import Path

import streamlit as st
from streamlit.testing.v1 import AppTest
from test_recherche import CountingEmbeddings, RecordingModel, Upload

from revisiter import config, encoders, ollama_client
from revisiter.config import Settings


def test_suggestion_attend_des_fichiers_et_envoie_une_seule_question(monkeypatch):
    uploads = []
    modele = RecordingModel()
    encodeur = CountingEmbeddings()
    monkeypatch.setattr(st, "file_uploader", lambda *a, **kw: list(uploads))
    monkeypatch.setattr(config, "load_settings", lambda: Settings("local", "modele-test"))
    monkeypatch.setattr(encoders, "TransformerEmbeddings", lambda encoder: encodeur)
    monkeypatch.setattr(ollama_client, "create_chat_model", lambda settings: modele)
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"))
    app.run(timeout=15)
    assert not app.exception
    assert app.button(key="suggestion_role").disabled
    assert not modele.calls
    uploads.append(Upload("demo.py", b"# cible_panier\na = 1"))
    app.run(timeout=15)
    app.button(key="suggestion_role").click().run(timeout=15)
    assert not app.exception
    assert not app.error
    assert not modele.calls
    assert len(app.session_state["messages"]) == 2
    assert "rôle de chacun des fichiers importés" in app.session_state["messages"][0]["content"]
    assert app.session_state["messages"][1]["sources"] == ["demo.py:L1-L1"]
    assert app.session_state["messages"][1]["metrics"]["method"] == "static"
    app.run(timeout=15)
    assert not modele.calls
    assert len(encodeur.document_calls) == 0
