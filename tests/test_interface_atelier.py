"""Vérifier la transition vers la discussion et les références affichées en HTML."""

from html.parser import HTMLParser
from pathlib import Path

import streamlit as st
from streamlit.testing.v1 import AppTest
from test_rapidite import SansEncodeur
from test_recherche import RecordingModel, Upload

from revisiter import config, encoders, ollama_client
from revisiter.config import Settings
from revisiter.interface import sources_message


def test_accueil_retire_des_le_premier_message_et_retabli_apres_effacement(monkeypatch):
    modele = RecordingModel()
    monkeypatch.setattr(st, "file_uploader", lambda *a, **kw: [Upload("demo.py", b"a = 1")])
    monkeypatch.setattr(config, "load_settings", lambda: Settings("local", "modele-test"))
    monkeypatch.setattr(encoders, "TransformerEmbeddings", lambda encoder: SansEncodeur())
    monkeypatch.setattr(ollama_client, "create_chat_model", lambda settings: modele)
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py")).run(timeout=15)
    assert app.button(key="suggestion_role")
    app.button(key="suggestion_role").click().run(timeout=15)
    assert not app.exception and not app.error
    assert not modele.calls
    assert not any(b.key == "suggestion_role" for b in app.button)
    assert len(app.session_state["messages"]) == 2
    assert app.session_state["messages"][1]["metrics"]["method"] == "static"
    assert app.session_state["messages"][1]["sources"] == ["demo.py:L1-L1"]
    assert any(e.label == "Passages fournis à l’assistant" for e in app.expander)
    app.button(key="effacer_discussion").click().run(timeout=15)
    assert not app.exception
    assert app.session_state["messages"] == []
    assert not modele.calls
    assert app.button(key="suggestion_role")


def test_les_references_sont_du_texte_meme_si_le_nom_contient_du_html(monkeypatch):
    blocs = []
    monkeypatch.setattr(st, "html", lambda contenu: blocs.append(contenu))
    reference = 'x<script>alert(1)</script><a href="x">.py:L1-L2'
    sources_message([reference, reference])

    class Lecture(HTMLParser):
        def __init__(self):
            super().__init__()
            self.balises = []
            self.textes = []

        def handle_starttag(self, tag, attrs):
            self.balises.append(tag)

        def handle_data(self, data):
            self.textes.append(data)

    lecture = Lecture()
    lecture.feed(blocs[0])
    assert "script" not in lecture.balises and "a" not in lecture.balises
    assert "".join(lecture.textes).count(reference) == 1
