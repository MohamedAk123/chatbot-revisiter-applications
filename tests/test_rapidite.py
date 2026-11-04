"""Vérifier l'affichage progressif et les optimisations sans perdre les références."""

import re
import sys
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
import streamlit as st
from langchain_core.embeddings import Embeddings
from langchain_core.messages import AIMessageChunk
from streamlit.testing.v1 import AppTest
from test_recherche import CountingEmbeddings, RecordingModel, Upload

from revisiter import config, encoders, ollama_client
from revisiter.config import Settings
from revisiter.encoders import TransformerEmbeddings
from revisiter.ingestion import Chunk
from revisiter.ollama_client import create_chat_model
from revisiter.workflow import build_question_graph, iterer_reponse


class SansEncodeur(Embeddings):
    def embed_documents(self, texts):
        raise AssertionError("Aucune indexation n’est nécessaire pour ce contexte complet.")

    def embed_query(self, text):
        raise AssertionError("Aucune recherche n’est nécessaire pour ce contexte complet.")


def test_petits_imports_transmis_entierement_sans_charger_l_encodeur():
    chunks = [Chunk("a.py", 1, 1, "a = 1"), Chunk("b.py", 1, 1, "b = 2")]
    graphe = build_question_graph(chunks, SansEncodeur(), RecordingModel(), top_k=2)
    resultat = graphe.invoke({"question": "Explique le code."})
    assert resultat["sources"] == [c.reference for c in chunks]
    assert all(c.text in resultat["context"] for c in chunks)
    assert len(resultat["context"]) <= 6_000


@pytest.mark.parametrize(("nombre", "top_k", "budget"), [(3, 1, 6_000), (2, 2, 30)])
def test_recherche_conservee_si_le_nombre_ou_le_budget_impose_une_selection(nombre, top_k, budget):
    encodeur = CountingEmbeddings()
    chunks = [Chunk(f"f{i}.py", 1, 1, "a = 1") for i in range(nombre)]
    graphe = build_question_graph(chunks, encodeur, RecordingModel(),
                                 top_k=top_k, max_context_chars=budget)
    resultat = graphe.invoke({"question": "Explique."})
    assert len(encodeur.document_calls) == len(encodeur.query_calls) == 1
    assert len(resultat["sources"]) < nombre
    assert len(resultat["context"]) <= budget


def test_premier_fragment_disponible_avant_la_fin_de_la_generation():
    continuer = Event()

    class Modele:
        def stream(self, messages):
            yield AIMessageChunk(content="Première partie. ")
            if not continuer.wait(5):
                raise RuntimeError("Le flux attendait la fin avant d’afficher le début.")
            yield AIMessageChunk(content="Suite [demo.py:L1-L1].")

    graphe = build_question_graph([Chunk("demo.py", 1, 1, "a = 1")], SansEncodeur(), Modele())
    resultat = {}
    flux = iterer_reponse(graphe, {"question": "Explique."}, resultat)
    try:
        premier = next(flux)
        assert premier == "Première partie. "
        assert "answer" not in resultat
        assert resultat["sources"] == ["demo.py:L1-L1"]
    finally:
        continuer.set()
    suite = "".join(flux)
    assert resultat["answer"] == premier + suite
    assert "[demo.py:L1-L1]" in resultat["answer"]


def test_erreur_apres_un_fragment_ne_produit_pas_de_reponse_finale():
    class Modele:
        def stream(self, messages):
            yield AIMessageChunk(content="Début incomplet")
            raise ConnectionError("Le service s’est interrompu.")

    graphe = build_question_graph([], SansEncodeur(), Modele())
    resultat = {}
    flux = iterer_reponse(graphe, {"question": "Bonjour"}, resultat)
    assert next(flux) == "Début incomplet"
    with pytest.raises(ConnectionError):
        list(flux)
    assert "answer" not in resultat


def test_interface_retire_reponse_partielle_et_garde_historique_et_export_coherents(monkeypatch):
    class Modele(RecordingModel):
        def stream(self, messages):
            self.calls.append(messages)
            if self.fail:
                yield AIMessageChunk(content="FRAGMENT_INCOMPLET")
                raise ConnectionError("Échec après le début de la réponse.")
            refs = re.findall(r"\[([^\]\n]+:L\d+-L\d+)\]", messages[-1].content)
            yield AIMessageChunk(content="Réponse ")
            yield AIMessageChunk(content=f"complète [{refs[0]}].")

    modele = Modele()
    modele.fail = True
    monkeypatch.setattr(st, "file_uploader", lambda *a, **kw: [Upload("demo.py", b"a = 1")])
    monkeypatch.setattr(config, "load_settings", lambda: Settings("local", "modele-test"))
    monkeypatch.setattr(encoders, "TransformerEmbeddings", lambda encoder: SansEncodeur())
    monkeypatch.setattr(ollama_client, "create_chat_model", lambda settings: modele)
    telechargements = []
    monkeypatch.setattr(st, "download_button", lambda *a, **kw: telechargements.append(kw))
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"))
    app.run(timeout=15)
    app.chat_input[0].set_value("Explique.").run(timeout=15)
    assert not app.exception
    assert app.error
    assert app.session_state["messages"] == []
    assert not any("FRAGMENT_INCOMPLET" in m.value for m in app.markdown)
    assert "FRAGMENT_INCOMPLET" not in telechargements[-1]["data"].decode("utf-8-sig")
    modele.fail = False
    app.chat_input[0].set_value("Explique encore.").run(timeout=15)
    assert not app.exception
    assert not app.error
    assert len(app.session_state["messages"]) == 2
    reponse = app.session_state["messages"][-1]["content"]
    assert reponse == "Réponse complète [demo.py:L1-L1]."
    assert reponse in telechargements[-1]["data"].decode("utf-8-sig")
    assert len([m for m in app.chat_message if m.name == "assistant"]) == 1


@pytest.mark.parametrize("cache_manquant", [None, "tokenizer", "model"])
def test_cache_local_prioritaire_et_telechargement_seulement_si_necessaire(monkeypatch, cache_manquant):
    appels = []
    tokenizer = object()

    class Modele:
        def eval(self):
            return self

    modele = Modele()

    def charger(nom, resultat):
        def from_pretrained(identifiant, **kwargs):
            appels.append((nom, kwargs["local_files_only"]))
            assert kwargs["trust_remote_code"] is False
            if cache_manquant == nom and kwargs["local_files_only"]:
                raise OSError("Cache incomplet pour cet essai.")
            return resultat
        return SimpleNamespace(from_pretrained=from_pretrained)

    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(
        AutoTokenizer=charger("tokenizer", tokenizer), AutoModel=charger("model", modele),
    ))
    encodeur = TransformerEmbeddings()
    encodeur._load()
    assert appels[0] == ("tokenizer", True)
    assert any(not local for _, local in appels) is (cache_manquant is not None)
    assert encodeur._model is modele
    assert encodeur._tokenizer is tokenizer
    nombre = len(appels)
    encodeur._load()
    assert len(appels) == nombre


def test_generation_structuree_non_tronquee_et_modele_local_garde_en_memoire():
    modele = create_chat_model(Settings("local", "modele-test"))
    assert modele.keep_alive == "15m"
    assert modele.num_predict is None
