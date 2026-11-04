"""Vérifier la recherche, la synthèse et l'interface sans appel réel aux modèles."""

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

import pytest
import streamlit as st
from langchain_core.embeddings import Embeddings
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage
from langchain_core.utils.function_calling import convert_to_json_schema
from streamlit.testing.v1 import AppTest

from revisiter import config, encoders, ollama_client
from revisiter.config import Settings
from revisiter.ingestion import Chunk
from revisiter.recommandations import (
    ErreurRecommandations,
    ReponseRecommandations,
    build_recommendation_graph,
    valider_recommandations,
)
from revisiter.workflow import MAX_CONTEXT_CHARS, build_question_graph, build_summary_graph


class CountingEmbeddings(Embeddings):
    def __init__(self, encoder="camembert"):
        self.encoder = encoder
        self.document_calls = []
        self.query_calls = []

    def embed_documents(self, texts):
        self.document_calls.append(list(texts))
        return [[1.0, 0.0] if "cible_panier" in t else [0.0, 1.0] for t in texts]

    def embed_query(self, text):
        self.query_calls.append(text)
        return [1.0, 0.0]


class RecordingModel:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.structured_result = None
        self.structured_schema = None
        self.structured_method = None

    def invoke(self, messages):
        self.calls.append(messages)
        if self.fail:
            raise ConnectionError("Service indisponible pour ce test")
        refs = re.findall(r"\[([^\]\n]+:L\d+-L\d+)\]", messages[-1].content)
        texte = f"Réponse sourcée [{refs[0]}]." if refs else "Réponse générale."
        return AIMessage(content=texte)

    def with_structured_output(self, schema, *, method):
        self.structured_schema = schema
        self.structured_method = method
        parent = self

        class StructuredRecordingModel:
            def invoke(self, messages):
                parent.invoke(messages)
                if isinstance(parent.structured_result, Exception):
                    raise parent.structured_result
                if parent.structured_result is not None:
                    return deepcopy(parent.structured_result)
                refs = re.findall(r"\[([^\]\n]+:L\d+-L\d+)\]", messages[-1].content)
                resultat = donnees_recommandations(refs[0])
                if "limites" not in parent.structured_schema["properties"]:
                    resultat.pop("limites")
                return resultat

        return StructuredRecordingModel()


def test_contexte_borne_et_index_reutilise():
    embeddings = CountingEmbeddings()
    model = RecordingModel()
    chunks = [Chunk("cible.py", 1, 1, "cible_panier " + "x" * 1_100)]
    chunks.extend(Chunk(f"autre{i}.py", 1, 1, "y" * 1_100) for i in range(10))
    graph = build_question_graph(chunks, embeddings, model, top_k=10)
    for question in ("Comment calculer le total ?", "Explique ce calcul."):
        result = graph.invoke({"question": question})
        assert len(result["context"]) <= MAX_CONTEXT_CHARS
        assert result["sources"][0] == "cible.py:L1-L1"
        assert 1 < len(result["sources"]) < len(chunks)
        assert result["context"] in model.calls[-1][-1].content
    assert len(embeddings.document_calls) == 1
    assert len(embeddings.query_calls) == 2


def test_references_et_separateurs_comptent_dans_le_budget():
    chunks = [
        Chunk("cible_panier.py", 1, 1, "cible_panier"),
        Chunk("b.py", 1, 1, "b"),
    ]
    budget = len(f"[{chunks[0].reference}]\n{chunks[0].text}")
    result = build_question_graph(
        chunks, CountingEmbeddings(), RecordingModel(),
        top_k=2, max_context_chars=budget,
    ).invoke({"question": "total"})
    assert len(result["context"]) == budget
    assert result["sources"] == [chunks[0].reference]


def test_nom_historique_et_question_courante_transmis():
    model = RecordingModel()
    graph = build_question_graph([], CountingEmbeddings(), model)
    graph.invoke({
        "question": "Quel est le nom du projet ?",
        "project_name": "Atlas",
        "history": [
            {"role": "user", "content": "Nous travaillons sur un panier."},
            {"role": "assistant", "content": "D’accord."},
        ],
    })
    messages = model.calls[-1]
    assert messages[1].content == "Nous travaillons sur un panier."
    assert messages[2].content == "D’accord."
    assert "Nom du projet : Atlas" in messages[-1].content
    assert "Quel est le nom du projet ?" in messages[-1].content
    assert "Aucun extrait fourni." in messages[-1].content


def test_historique_ne_grossit_pas_indefiniment_le_prompt():
    model = RecordingModel()
    graph = build_question_graph([], CountingEmbeddings(), model)
    graph.invoke({"question": "Suite ?", "history": [
        {"role": "user", "content": "ancien" * 1_000},
        {"role": "assistant", "content": "récent"},
    ]})
    assert [m.content for m in model.calls[-1][1:-1]] == ["récent"]


def test_discussion_sans_fichier_ne_charge_pas_les_vecteurs():
    embeddings = CountingEmbeddings()
    result = build_question_graph([], embeddings, RecordingModel()).invoke({"question": "Bonjour"})
    assert result["sources"] == []
    assert embeddings.document_calls == embeddings.query_calls == []


@dataclass
class Upload:
    name: str
    data: bytes

    @property
    def size(self):
        return len(self.data)

    def getvalue(self):
        return self.data


@pytest.fixture
def interface(monkeypatch):
    uploads = []
    embeddings = CountingEmbeddings()
    model = RecordingModel()
    monkeypatch.setattr(st, "file_uploader", lambda *args, **kwargs: list(uploads))
    monkeypatch.setattr(config, "load_settings", lambda: Settings("local", "modele-test"))
    monkeypatch.setattr(encoders, "TransformerEmbeddings", lambda encoder: embeddings)
    monkeypatch.setattr(ollama_client, "create_chat_model", lambda settings: model)
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"))
    return app, uploads, embeddings, model


def test_interface_notebook_long_indexe_une_fois_et_montre_les_passages(interface):
    app, uploads, embeddings, model = interface
    cellules = [{"cell_type": "code", "source": "# documentation autre\n" + "x" * 1_000}
                for _ in range(12)]
    cellules.append({
        "cell_type": "code",
        "source": "def cible_panier(prix):\n    # Calcule le total\n    return sum(prix)",
        "outputs": [{"text": "SORTIE_NON_INDEXEE"}],
    })
    uploads.append(Upload("essai.ipynb", json.dumps({"nbformat": 4, "cells": cellules}).encode()))
    app.run(timeout=15)
    app.text_input[0].set_value("Atlas").run(timeout=15)
    app.chat_input[0].set_value("Comment est calculé le panier ?").run(timeout=15)
    assert not app.exception
    assert not app.error
    reponse = app.session_state["messages"][-1]
    assert "essai.ipynb#cellule13:L1-L3" in reponse["sources"]
    assert len(reponse["context"]) <= MAX_CONTEXT_CHARS
    assert "SORTIE_NON_INDEXEE" not in "\n".join(embeddings.document_calls[0])
    assert any(e.label == "Passages fournis à l’assistant" for e in app.expander)
    assert "Atlas" in model.calls[-1][-1].content
    app.chat_input[0].set_value("Explique encore le panier.").run(timeout=15)
    assert not app.exception
    assert len(embeddings.document_calls) == 1
    assert len(app.session_state["messages"]) == 4
    assert model.calls[-1][1].content == "Comment est calculé le panier ?"


def test_remplacement_meme_nom_et_suppression_invalident_index_et_historique(interface):
    app, uploads, embeddings, _ = interface
    uploads.append(Upload("demo.py", (b"# cible_panier ancien\na = 1" + b"\n# documentation" * 500)))
    app.run(timeout=15)
    app.chat_input[0].set_value("Explique le panier.").run(timeout=15)
    uploads[0] = Upload("demo.py", (b"# cible_panier nouveau\na = 2" + b"\n# documentation" * 500))
    app.run(timeout=15)
    assert app.session_state["messages"] == []
    app.chat_input[0].set_value("Explique le nouveau panier.").run(timeout=15)
    assert not app.exception
    assert len(embeddings.document_calls) == 2
    assert "nouveau" in app.session_state["messages"][-1]["context"]
    assert "ancien" not in app.session_state["messages"][-1]["context"]
    uploads.clear()
    app.run(timeout=15)
    assert app.session_state["messages"] == []
    assert "graphe" not in app.session_state
    app.chat_input[0].set_value("Bonjour").run(timeout=15)
    assert not app.exception
    assert app.session_state["messages"][-1]["sources"] == []
    assert len(embeddings.document_calls) == 2


def test_nom_projet_et_effacer_discussion_ne_reindexent_pas_les_memes_fichiers(interface):
    app, uploads, embeddings, model = interface
    uploads.append(Upload("demo.py", (b"# cible_panier\na = 1" + b"\n# documentation" * 500)))
    app.run(timeout=15)
    app.chat_input[0].set_value("Explique le panier.").run(timeout=15)
    app.text_input[0].set_value("Nova").run(timeout=15)
    assert app.session_state["messages"] == []
    app.chat_input[0].set_value("Quel est le nom du projet ?").run(timeout=15)
    assert "Nom du projet : Nova" in model.calls[-1][-1].content
    app.button(key="effacer_discussion").click().run(timeout=15)
    assert app.session_state["messages"] == []
    app.chat_input[0].set_value("Encore une question.").run(timeout=15)
    assert not app.exception
    assert len(embeddings.document_calls) == 1


def test_erreur_ollama_ne_pollue_pas_historique_et_permet_reessai(interface):
    app, uploads, embeddings, model = interface
    uploads.append(Upload("demo.py", b"# cible_panier\na = 1"))
    app.run(timeout=15)
    model.fail = True
    app.chat_input[0].set_value("Explique le panier.").run(timeout=15)
    assert not app.exception
    assert len(app.error) == 1
    assert app.session_state["messages"] == []
    model.fail = False
    app.chat_input[0].set_value("Explique le panier.").run(timeout=15)
    assert not app.exception
    assert len(app.session_state["messages"]) == 2
    assert len(embeddings.document_calls) == 0


def test_vue_repartit_les_extraits_entre_fichiers_et_borne_le_contexte():
    chunks = [
        Chunk("a.py", 1, 1, "a" * 40),
        Chunk("a.py", 2, 2, "a" * 40),
        Chunk("a.py", 3, 3, "a" * 40),
        Chunk("b.py", 1, 1, "b" * 40),
    ]
    budget = sum(len(f"[{c.reference}]\n{c.text}") for c in (chunks[0], chunks[-1])) + 2
    model = RecordingModel()
    result = build_summary_graph(chunks, model, max_context_chars=budget).invoke({
        "project_name": "Atlas",
    })
    assert result["sources"] == ["a.py:L1-L1", "b.py:L1-L1"]
    assert result["total_chunks"] == 4
    assert result["selected_chunks"] == 2
    assert result["covered_files"] == ["a.py", "b.py"]
    assert result["omitted_files"] == []
    assert len(result["context"]) == budget
    assert "Nom du projet : Atlas" in model.calls[-1][-1].content
    assert "Extraits sélectionnés : 2 / 4" in model.calls[-1][-1].content


def test_vue_compte_un_notebook_comme_un_fichier_et_repartit_les_cellules():
    chunks = [Chunk(f"essai.ipynb#cellule{i}", 1, 2, f"# cellule {i}\na = {i}")
              for i in range(1, 6)]
    result = build_summary_graph(chunks, RecordingModel()).invoke({})
    assert result["total_files"] == 1
    assert result["covered_files"] == ["essai.ipynb"]
    assert result["sources"][:3] == [
        "essai.ipynb#cellule1:L1-L2",
        "essai.ipynb#cellule3:L1-L2",
        "essai.ipynb#cellule5:L1-L2",
    ]
    assert result["selected_chunks"] == 5


def test_vue_signale_les_fichiers_omis_sans_les_inventer_dans_le_prompt():
    chunks = [Chunk("petit.py", 1, 1, "a = 1"), Chunk("grand.py", 1, 1, "x" * 200)]
    model = RecordingModel()
    result = build_summary_graph(chunks, model, max_context_chars=50).invoke({})
    assert result["total_files"] == 2
    assert result["covered_files"] == ["petit.py"]
    assert result["omitted_files"] == ["grand.py"]
    assert "Fichiers représentés : 1 / 2" in model.calls[-1][-1].content
    assert "grand.py" not in model.calls[-1][-1].content


def test_vue_sans_extrait_utilisable_ne_fait_pas_appel_au_modele():
    model = RecordingModel()
    with pytest.raises(ValueError):
        build_summary_graph([], model)
    with pytest.raises(ValueError):
        build_summary_graph([Chunk("grand.py", 1, 1, "x" * 200)], model, 10).invoke({})
    assert model.calls == []


def test_bouton_vue_desactive_sans_fichier(interface):
    app, _, _, model = interface
    app.run(timeout=15)
    assert not app.exception
    assert app.button(key="preparer_vue").disabled
    assert "vue_ensemble" not in app.session_state
    assert model.calls == []


def test_vue_reste_visible_pendant_discussion_et_ne_modifie_pas_historique(interface):
    app, uploads, embeddings, model = interface
    uploads.append(Upload("demo.py", b"# cible_panier\na = 1"))
    app.run(timeout=15)
    app.text_input[0].set_value("Atlas").run(timeout=15)
    app.button(key="preparer_vue").click().run(timeout=15)
    assert not app.exception
    vue = app.session_state["vue_ensemble"]
    assert vue["sources"] == ["demo.py:L1-L2"]
    assert app.session_state["messages"] == []
    assert embeddings.document_calls == []
    assert len(model.calls) == 1
    assert any(e.label == "Passages fournis pour la vue d’ensemble" for e in app.expander)
    app.chat_input[0].set_value("Explique le panier.").run(timeout=15)
    assert not app.exception
    assert len(model.calls) == 2
    assert len(embeddings.document_calls) == 0
    assert app.session_state["vue_ensemble"] == vue
    assert len(app.session_state["messages"]) == 2
    app.button(key="effacer_discussion").click().run(timeout=15)
    assert app.session_state["messages"] == []
    assert app.session_state["vue_ensemble"] == vue
    assert len(model.calls) == 2


def test_vue_invalidee_si_contenu_ou_nom_projet_change(interface):
    app, uploads, _, model = interface
    uploads.append(Upload("demo.py", (b"# cible_panier ancien\na = 1" + b"\n# documentation" * 500)))
    app.run(timeout=15)
    app.button(key="preparer_vue").click().run(timeout=15)
    assert "ancien" in app.session_state["vue_ensemble"]["context"]
    uploads[0] = Upload("demo.py", (b"# cible_panier nouveau\na = 2" + b"\n# documentation" * 500))
    app.run(timeout=15)
    assert "vue_ensemble" not in app.session_state
    app.button(key="preparer_vue").click().run(timeout=15)
    assert "nouveau" in app.session_state["vue_ensemble"]["context"]
    app.text_input[0].set_value("Nova").run(timeout=15)
    assert "vue_ensemble" not in app.session_state
    app.button(key="preparer_vue").click().run(timeout=15)
    assert not app.exception
    assert "Nom du projet : Nova" in model.calls[-1][-1].content
    uploads.clear()
    app.run(timeout=15)
    assert "vue_ensemble" not in app.session_state
    assert app.button(key="preparer_vue").disabled


def test_vue_erreur_fournisseur_permet_reessai(interface):
    app, uploads, _, model = interface
    uploads.append(Upload("demo.py", b"# cible_panier\na = 1"))
    app.run(timeout=15)
    model.fail = True
    app.button(key="preparer_vue").click().run(timeout=15)
    assert not app.exception
    assert len(app.error) == 1
    assert "vue_ensemble" not in app.session_state
    assert app.session_state["messages"] == []
    model.fail = False
    app.button(key="preparer_vue").click().run(timeout=15)
    assert not app.exception
    assert not app.error
    assert "vue_ensemble" in app.session_state


def donnees_recommandations(reference="demo.py:L1-L2"):
    return {
        "recommandations": [{
            "titre": "Tester le comportement présenté",
            "justification": "L’extrait présente le calcul du panier.",
            "proposition": "Ajouter des tests sur une liste vide et des prix négatifs.",
            "benefice": "Documenter et préserver les comportements attendus.",
            "priorite": "moyenne",
            "effort": "faible",
            "references": [reference],
        }],
        "limites": "Les autres fichiers et les éventuels tests restent à examiner.",
    }


def test_recommandations_triees_et_references_normalisees_sans_modifier_reponse():
    donnees = donnees_recommandations()
    for priorite in ("basse", "haute"):
        autre = deepcopy(donnees["recommandations"][0])
        autre["priorite"] = priorite
        donnees["recommandations"].append(autre)
    donnees["recommandations"][0]["references"] = ["[demo.py:L1-L2]", "demo.py:L1-L2"]
    avant = deepcopy(donnees)
    resultat = valider_recommandations(donnees, ["demo.py:L1-L2"])
    assert [r["priorite"] for r in resultat["recommandations"]] == ["haute", "moyenne", "basse"]
    assert all(r["references"] == ["demo.py:L1-L2"] for r in resultat["recommandations"])
    assert donnees == avant


@pytest.mark.parametrize(("champ", "valeur"), [
    ("references", ["invente.py:L1-L2"]),
    ("references", ["demo.py:L99-L100"]),
    ("references", []),
    ("references", "demo.py:L1-L2"),
    ("priorite", "urgente"),
    ("effort", "deux jours"),
    ("justification", ""),
    ("proposition", 42),
])
def test_recommandation_invalide_ou_reference_inventee_rejetee(champ, valeur):
    donnees = donnees_recommandations()
    donnees["recommandations"][0][champ] = valeur
    with pytest.raises(ValueError):
        valider_recommandations(donnees, ["demo.py:L1-L2"])


def test_reponse_structuree_incomplete_ou_trop_de_propositions_rejetee():
    with pytest.raises(ValueError):
        valider_recommandations({"answer": "Du texte libre"}, ["demo.py:L1-L2"])
    donnees = donnees_recommandations()
    donnees["recommandations"] *= 4
    with pytest.raises(ValueError):
        valider_recommandations(donnees, ["demo.py:L1-L2"])
    donnees = donnees_recommandations()
    del donnees["recommandations"][0]["benefice"]
    with pytest.raises(ValueError):
        valider_recommandations(donnees, ["demo.py:L1-L2"])


def test_liste_vide_acceptee_avec_limites_explicites():
    donnees = {"recommandations": [], "limites": "Les extraits ne justifient pas de proposition."}
    assert valider_recommandations(donnees, []) == donnees


def test_graphe_recommandations_borne_et_schema_json_utilise():
    model = RecordingModel()
    chunks = [Chunk(f"f{i}.py", 1, 1, "code " + "x" * 1_100) for i in range(10)]
    resultat = build_recommendation_graph(chunks, model).invoke({"project_name": "Atlas"})
    schema = model.structured_schema
    assert set(schema["required"]) == {"recommandations"}
    assert schema["properties"]["recommandations"]["maxItems"] == 3
    references = schema["properties"]["recommandations"]["items"]["properties"]["references"]
    assert references["items"]["enum"] == sorted(set(resultat["sources"]))
    assert references["minItems"] == 1
    assert model.structured_method == "json_schema"
    assert len(resultat["context"]) <= MAX_CONTEXT_CHARS
    assert resultat["selected_chunks"] < resultat["total_chunks"]
    assert resultat["omitted_files"]
    assert resultat["recommandations"][0]["references"][0] in resultat["sources"]
    assert "Nom du projet : Atlas" in model.calls[-1][-1].content


def test_bouton_recommandations_desactive_sans_fichier(interface):
    app, _, _, model = interface
    app.run(timeout=15)
    assert not app.exception
    assert app.button(key="proposer_ameliorations").disabled
    assert model.calls == []


def test_ameliorations_independantes_du_resume_et_de_la_discussion(interface):
    app, uploads, embeddings, model = interface
    uploads.append(Upload("demo.py", b"# cible_panier\na = 1"))
    app.run(timeout=15)
    app.button(key="preparer_vue").click().run(timeout=15)
    vue = app.session_state["vue_ensemble"]
    app.button(key="proposer_ameliorations").click().run(timeout=15)
    assert not app.exception
    assert not app.error
    analyse = app.session_state["ameliorations"]
    assert analyse["recommandations"][0]["references"] == ["demo.py:L1-L2"]
    assert app.session_state["messages"] == []
    assert app.session_state["vue_ensemble"] == vue
    assert embeddings.document_calls == []
    assert any(e.label == "Passages fournis pour les recommandations" for e in app.expander)
    app.chat_input[0].set_value("Explique le panier.").run(timeout=15)
    assert not app.exception
    assert len(app.session_state["messages"]) == 2
    assert app.session_state["ameliorations"] == analyse
    assert len(embeddings.document_calls) == 0
    app.button(key="effacer_discussion").click().run(timeout=15)
    assert app.session_state["messages"] == []
    assert app.session_state["ameliorations"] == analyse
    assert len(model.calls) == 3


def test_recommandations_invalidees_par_changement_du_projet(interface):
    app, uploads, _, _ = interface
    uploads.append(Upload("demo.py", (b"# cible_panier ancien\na = 1" + b"\n# documentation" * 500)))
    app.run(timeout=15)
    app.button(key="proposer_ameliorations").click().run(timeout=15)
    assert "ameliorations" in app.session_state
    uploads[0] = Upload("demo.py", (b"# cible_panier nouveau\na = 2" + b"\n# documentation" * 500))
    app.run(timeout=15)
    assert "ameliorations" not in app.session_state
    app.button(key="proposer_ameliorations").click().run(timeout=15)
    assert "nouveau" in app.session_state["ameliorations"]["context"]
    app.text_input[0].set_value("Nova").run(timeout=15)
    assert "ameliorations" not in app.session_state
    app.button(key="proposer_ameliorations").click().run(timeout=15)
    uploads.clear()
    app.run(timeout=15)
    assert not app.exception
    assert "ameliorations" not in app.session_state


def test_recommandations_erreur_fournisseur_reference_inventee_et_reessai(interface):
    app, uploads, _, model = interface
    uploads.append(Upload("demo.py", b"# cible_panier\na = 1"))
    app.run(timeout=15)
    model.fail = True
    app.button(key="proposer_ameliorations").click().run(timeout=15)
    assert not app.exception
    assert len(app.error) == 1
    assert "Impossible de joindre Ollama" in app.error[0].value
    assert "ameliorations" not in app.session_state
    model.fail = False
    model.structured_result = donnees_recommandations("invente.py:L1-L9")
    app.button(key="proposer_ameliorations").click().run(timeout=15)
    assert not app.exception
    assert len(app.error) == 1
    assert "référence" in app.error[0].value
    assert "ameliorations" not in app.session_state
    model.structured_result = None
    app.button(key="proposer_ameliorations").click().run(timeout=15)
    assert not app.exception
    assert not app.error
    assert "ameliorations" in app.session_state
    assert app.session_state["messages"] == []


def test_aucune_proposition_affiche_limites_sans_rajouter_de_recommandation(interface):
    app, uploads, _, model = interface
    uploads.append(Upload("demo.py", b"# cible_panier\na = 1"))
    model.structured_result = {"recommandations": [], "limites": "Informations insuffisantes."}
    app.run(timeout=15)
    app.button(key="proposer_ameliorations").click().run(timeout=15)
    assert not app.exception
    assert not app.error
    assert app.session_state["ameliorations"]["recommandations"] == []
    assert any("Aucune amélioration étayée" in info.value for info in app.info)


def test_schema_reel_langchain_exige_tous_les_champs_sans_fausses_valeurs_par_defaut():
    schema = convert_to_json_schema(ReponseRecommandations)
    assert set(schema["required"]) == {"recommandations", "limites"}
    proposition = schema["properties"]["recommandations"]["items"]
    assert set(proposition["required"]) == {
        "titre", "justification", "proposition", "benefice", "priorite", "effort", "references"
    }
    assert all("default" not in champ for champ in proposition["properties"].values())
    assert "description" in proposition["properties"]["references"]
    assert proposition["properties"]["references"]["type"] == "array"


@pytest.mark.parametrize(("reference", "sources", "valide"), [
    ("demo.py:L10-L12", ["demo.py:L1-L12"], True),
    ("demo.py:L2-L4", ["demo.py:L1-L2", "demo.py:L3-L5"], True),
    ("demo.py:L2-L5", ["demo.py:L1-L3", "demo.py:L3-L6"], True),
    ("essai.ipynb#cellule2:L2-L3", ["essai.ipynb#cellule2:L1-L5"], True),
    ("demo.py:L2-L4", ["demo.py:L1-L2", "demo.py:L4-L5"], False),
    ("demo.py:L2-L5", ["demo.py:L1-L3", "autre.py:L4-L6"], False),
    ("essai.ipynb#cellule2:L2-L4", [
        "essai.ipynb#cellule1:L1-L5", "essai.ipynb#cellule2:L1-L2"
    ], False),
    ("demo.py:L3-L2", ["demo.py:L1-L5"], False),
    ("demo.py:L0-L1", ["demo.py:L1-L5"], False),
])
def test_references_precises_uniquement_si_toutes_les_lignes_ont_ete_fournies(
    reference, sources, valide
):
    donnees = donnees_recommandations(reference)
    if valide:
        resultat = valider_recommandations(donnees, sources)
        assert resultat["recommandations"][0]["references"] == [reference]
    else:
        with pytest.raises(ErreurRecommandations):
            valider_recommandations(donnees, sources)


def test_erreur_de_decodage_du_modele_devient_un_message_controle():
    model = RecordingModel()
    model.structured_result = OutputParserException("Donnees de diagnostic non affichables")
    graph = build_recommendation_graph([Chunk("demo.py", 1, 2, "a = 1\nb = 2")], model)
    with pytest.raises(ErreurRecommandations) as erreur:
        graph.invoke({})
    assert "format" in str(erreur.value)
    assert "diagnostic non affichables" not in str(erreur.value)


def test_schema_de_generation_limite_les_references_aux_extraits_selectionnes():
    model = RecordingModel()
    chunks = [
        Chunk("a.py", 1, 1, "a = 1"),
        Chunk("b.py", 1, 1, "b" * 200),
    ]
    graph = build_recommendation_graph(chunks, model, max_context_chars=40)
    resultat = graph.invoke({})
    references = model.structured_schema["properties"]["recommandations"]["items"][
        "properties"
    ]["references"]["items"]["enum"]
    assert resultat["sources"] == references == ["a.py:L1-L1"]
    assert resultat["omitted_files"] == ["b.py"]


def test_perimetre_calcule_meme_si_le_modele_conclut_a_tort_que_la_liste_est_complete():
    model = RecordingModel()
    model.structured_result = donnees_recommandations("a.py:L1-L1")
    model.structured_result["limites"] = "Il n'y a pas d'autres améliorations pour ce projet."
    result = build_recommendation_graph([
        Chunk("a.py", 1, 1, "a = 1"),
        Chunk("b.py", 1, 1, "b" * 200),
    ], model, max_context_chars=40).invoke({})
    assert "1/2 extraits" in result["limites"]
    assert "1/2 fichiers" in result["limites"]
    assert "peuvent être complétées" in result["limites"]
    assert "pas d'autres améliorations" not in result["limites"]


def test_generation_ne_demande_plus_au_modele_de_juger_les_limites_du_projet():
    model = RecordingModel()
    model.structured_result = {"recommandations": []}
    result = build_recommendation_graph([Chunk("a.py", 1, 1, "a = 1")], model).invoke({})
    assert "limites" not in model.structured_schema["properties"]
    assert result["recommandations"] == []
    assert "1/1 extraits" in result["limites"]
