"""Vérifier le schéma sourcé, sa discussion et son export sans service externe."""

from copy import deepcopy
from pathlib import Path

import pytest
import streamlit as st
from langchain_core.exceptions import OutputParserException
from streamlit.testing.v1 import AppTest
from test_rapidite import SansEncodeur
from test_recherche import RecordingModel, Upload

from revisiter import config, encoders, ollama_client
from revisiter.config import Settings
from revisiter.export import generer_compte_rendu
from revisiter.ingestion import Chunk
from revisiter.schemas import (
    ErreurSchema,
    choisir_format,
    code_mermaid,
    texte_schema,
    valider_schema,
)
from revisiter.workflow import build_question_graph, iterer_reponse


def proposition():
    return {
        "titre": "Calcul du total",
        "explication": "Lorsqu’elle est appelée, la fonction additionne les prix et retourne le total.",
        "noeuds": [
            {"libelle": "Liste des prix", "genre": "entree", "sources": [1]},
            {"libelle": "Calculer sum(prix)", "genre": "traitement", "sources": [1]},
            {"libelle": "Retourner le total", "genre": "sortie", "sources": [1]},
        ],
        "liens": [
            {"de": 1, "vers": 2, "libelle": "", "sources": [1]},
            {"de": 2, "vers": 3, "libelle": "résultat", "sources": [1]},
        ],
    }


@pytest.mark.parametrize(("question", "choix", "attendu"), [
    ("Fais un schéma de cette fonction.", "Auto", "schema"),
    ("Génère un diagramme explicatif.", "Auto", "schema"),
    ("Explique avec un schéma.", "Auto", "schema"),
    ("Explique l’étape 2 du schéma.", "Auto", "texte"),
    ("Explique sans schéma.", "Auto", "texte"),
    ("Fais un schéma.", "Texte", "texte"),
    ("Explique la fonction.", "Schéma", "schema"),
])
def test_format_explicite_et_suivi_sans_regeneration(question, choix, attendu):
    assert choisir_format(question, choix) == attendu


def test_references_notebook_et_arcs_conservent_les_passages_fournis():
    brut = proposition()
    brut["liens"][0]["sources"] = [2, 1, 2]
    schema = valider_schema(brut, ["demo.py:L4-L6", "test.ipynb#cellule2:L1-L3"])
    assert schema["liens"][0]["references"] == ["test.ipynb#cellule2:L1-L3", "demo.py:L4-L6"]
    assert schema["references"] == ["demo.py:L4-L6", "test.ipynb#cellule2:L1-L3"]


@pytest.mark.parametrize("erreur", ["source_absente", "source_booleenne", "arc_absent", "clic", "trop_blocs"])
def test_refuse_sources_inventees_topologie_invalide_et_instructions_graphiques(erreur):
    brut = proposition()
    if erreur == "source_absente":
        brut["noeuds"][0]["sources"] = [2]
    elif erreur == "source_booleenne":
        brut["liens"][0]["sources"] = [True]
    elif erreur == "arc_absent":
        brut["liens"][0]["vers"] = 4
    elif erreur == "clic":
        brut["noeuds"][0]["click"] = "https://exemple.invalid"
    else:
        brut["noeuds"] *= 3
    with pytest.raises(ErreurSchema):
        valider_schema(brut, ["demo.py:L4-L6"])


def test_libelles_restent_du_texte_dans_mermaid_et_export_markdown():
    brut = proposition()
    brut["noeuds"][1]["libelle"] = 'sum(prix) ["x"] <img src=x> ```; click N1'
    schema = valider_schema(brut, ["demo.py:L4-L6"])
    code = code_mermaid(schema)
    assert '<img' not in code and '```' not in code
    assert '#60;img' in code and '#96;#96;#96;' in code
    assert len([l for l in code.splitlines() if l.startswith("  N")]) == 5
    rapport = generer_compte_rendu("Démo", ["demo.py"], [{
        "role": "assistant", "content": texte_schema(schema), "schema": schema,
        "sources": schema["references"],
    }])
    assert f"```mermaid\n{code}\n```" in rapport
    assert "demo.py:L4-L6" in rapport


def test_un_seul_appel_structure_et_suivi_textuel_avec_blocs_dans_historique():
    modele = RecordingModel()
    modele.structured_result = proposition()
    graphe = build_question_graph([Chunk("demo.py", 4, 6, "return sum(prix)")], SansEncodeur(), modele)
    resultat = {}
    texte = "".join(iterer_reponse(graphe, {"question": "Fais un schéma.", "response_format": "schema"}, resultat))
    assert len(modele.calls) == 1
    assert modele.structured_method == "json_schema"
    assert modele.structured_schema["properties"]["noeuds"]["maxItems"] == 8
    assert "1: demo.py:L4-L6" in modele.calls[0][-1].content
    assert "return sum(prix)" in modele.calls[0][-1].content
    assert "2. Calculer sum(prix)" in texte
    suivi = graphe.invoke({"question": "Explique l’étape 2 du schéma.", "response_format": "texte",
                          "history": [{"role": "assistant", "content": texte}]})
    assert suivi["schema"] is None
    assert any(m.type == "ai" and "2. Calculer sum(prix)" in m.content for m in modele.calls[-1])


def test_sans_fichier_le_modele_n_est_pas_appele():
    modele = RecordingModel()
    graphe = build_question_graph([], SansEncodeur(), modele)
    with pytest.raises(ErreurSchema, match="Ajoutez au moins un fichier"):
        graphe.invoke({"question": "Fais un schéma", "response_format": "schema"})
    assert not modele.calls


def test_information_insuffisante_ne_fabrique_pas_de_blocs():
    modele = RecordingModel()
    modele.structured_result = {
        "titre": "Base de données non visible", "explication": "Aucune connexion n’est visible.",
        "noeuds": [], "liens": [],
    }
    resultat = build_question_graph([Chunk("demo.py", 1, 1, "a = 1")], SansEncodeur(), modele).invoke({
        "question": "Schéma de la base de données", "response_format": "schema",
    })
    assert not resultat["schema"]["noeuds"]
    assert "Aucune connexion" in resultat["answer"]


@pytest.fixture
def interface_schema(monkeypatch):
    modele = RecordingModel()
    modele.structured_result = proposition()
    fichiers = [Upload("demo.py", b"return sum(prix)")]
    monkeypatch.setattr(st, "file_uploader", lambda *a, **kw: fichiers)
    monkeypatch.setattr(config, "load_settings", lambda: Settings("local", "modele-test"))
    monkeypatch.setattr(encoders, "TransformerEmbeddings", lambda encoder: SansEncodeur())
    monkeypatch.setattr(ollama_client, "create_chat_model", lambda settings: modele)
    dessins, telechargements = [], []
    monkeypatch.setattr(st, "mermaid_chart", lambda code, **kw: dessins.append(code))
    monkeypatch.setattr(st, "download_button", lambda *a, **kw: telechargements.append(kw))
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py")).run(timeout=15)
    return app, modele, dessins, telechargements


def test_interface_conserve_schema_sans_regenerer_et_exporte_le_meme_code(interface_schema):
    app, modele, dessins, telechargements = interface_schema
    app.button(key="suggestion_schema").click().run(timeout=15)
    assert not app.exception and not app.error
    assert len(modele.calls) == 1 and len(dessins) == 1
    message = deepcopy(app.session_state["messages"][-1])
    assert message["schema"]["noeuds"][1]["libelle"] == "Calculer sum(prix)"
    code = code_mermaid(message["schema"])
    assert dessins[-1] == code
    assert any(t["file_name"].endswith(".mmd") and t["data"].decode("utf-8") == code for t in telechargements)
    assert code in telechargements[-1]["data"].decode("utf-8-sig")
    app.run(timeout=15)
    assert not app.exception
    assert len(modele.calls) == 1
    assert app.session_state["messages"][-1] == message
    app.chat_input[0].set_value("Explique l’étape 2 du schéma.").run(timeout=15)
    assert not app.exception and not app.error
    assert len(modele.calls) == 2
    assert "schema" not in app.session_state["messages"][-1]
    assert any(m.type == "ai" and "2. Calculer sum(prix)" in m.content for m in modele.calls[-1])
    app.button(key="effacer_discussion").click().run(timeout=15)
    assert app.session_state["messages"] == []
    assert not app.exception


@pytest.mark.parametrize("echec", ["source", "parse"])
def test_schema_invalide_n_est_ni_affiche_ni_enregistre(interface_schema, echec):
    app, modele, dessins, telechargements = interface_schema
    if echec == "source":
        modele.structured_result["noeuds"][0]["sources"] = [999]
    else:
        modele.structured_result = OutputParserException("Réponse brute à ne pas afficher")
    app.button(key="suggestion_schema").click().run(timeout=15)
    assert not app.exception and app.error
    assert not dessins and not app.session_state["messages"]
    assert "Réponse brute" not in " ".join(e.value for e in app.error)
    assert "```mermaid" not in telechargements[-1]["data"].decode("utf-8-sig")
