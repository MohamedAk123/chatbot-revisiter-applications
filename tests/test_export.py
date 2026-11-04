"""Vérifier que le compte rendu reflète l'activité et la progression actuelles."""

from copy import deepcopy
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest
from test_recherche import CountingEmbeddings, RecordingModel, Upload, donnees_recommandations

from revisiter import config, encoders, ollama_client
from revisiter.config import Settings
from revisiter.export import generer_compte_rendu
from revisiter.guide import build_guided_plan_graph, reprendre_guide
from revisiter.ingestion import Chunk


def analyse():
    return {
        "selected_chunks": 1, "total_chunks": 3,
        "covered_files": ["demo.py"], "total_files": 2, "omitted_files": ["autre.py"],
        "sources": ["demo.py:L1-L2"], "context": "CONTEXTE_BRUT_A_NE_PAS_SERIALISER",
    }


def test_export_vide_lisible_sans_inventer_une_analyse_ou_des_tests():
    texte = generer_compte_rendu("", [], [])
    assert "non renseigné" in texte
    assert "Aucun fichier importé." in texte
    assert "Aucune vue d’ensemble préparée." in texte
    assert "Aucune analyse d’améliorations préparée." in texte
    assert "Aucun plan préparé." in texte
    assert "Aucun message dans la discussion actuelle." in texte
    assert "UTC" in texte


def test_export_complet_coverage_sources_et_champs_lisibles_sans_serialiser_objets_internes():
    vue = {**analyse(), "answer": "Vue d’ensemble avec accents : quantité."}
    ameliorations = {**analyse(), **donnees_recommandations()}
    vue["objet_interne"] = object()
    vue["api_key"] = "VALEUR_DE_CONFIGURATION_A_NE_PAS_SERIALISER"
    messages = [{"role": "user", "content": "Explique le calcul."}, {
        "role": "assistant", "content": "Il utilise sum().", "sources": ["demo.py:L1-L2"],
        "context": "CONTEXTE_BRUT_A_NE_PAS_SERIALISER",
    }]
    texte = generer_compte_rendu(
        "Atlas", ["demo.py", "essai.ipynb"], messages,
        vue_ensemble=vue, ameliorations=ameliorations,
    )
    assert "Atlas" in texte
    assert "essai.ipynb" in texte
    assert "1 / 3 extraits ; 1 / 2 fichiers" in texte
    assert "Fichiers non représentés" in texte and "autre.py" in texte
    assert vue["answer"] in texte
    for champ in ("titre", "justification", "proposition", "benefice"):
        assert ameliorations["recommandations"][0][champ] in texte
    assert ameliorations["limites"] in texte
    assert "demo.py:L1-L2" in texte
    assert texte.index("Explique le calcul.") < texte.index("Il utilise sum().")
    assert "CONTEXTE_BRUT_A_NE_PAS_SERIALISER" not in texte
    assert "VALEUR_DE_CONFIGURATION_A_NE_PAS_SERIALISER" not in texte
    assert "object at" not in texte


def test_code_et_accents_conserves_sans_casser_les_blocs_du_compte_rendu():
    reponse = "Voici le code :\n```python\nx = 1 < 2\n```\n````\nÉté <script>texte</script>"
    texte = generer_compte_rendu(
        "Atlas\n# Faux titre", ["nom`particulier.py"],
        [{"role": "assistant", "content": reponse, "sources": []}],
    )
    assert reponse in texte
    assert "`````text\n" + reponse + "\n`````" in texte
    assert "```text\nAtlas\n# Faux titre\n```" in texte
    assert "````text\nnom`particulier.py\n````" not in texte  # La clôture minimale suffit ici.
    assert "```text\nnom`particulier.py\n```" in texte


def test_export_plan_partiel_reformule_confirme_et_saute_sans_muter_la_session():
    proposition = donnees_recommandations()["recommandations"][0]
    graphe = build_guided_plan_graph([Chunk("demo.py", 1, 2, "a = 1\nb = 2")], proposition)
    configuration = {"configurable": {"thread_id": "test-export"}}
    sortie = graphe.invoke({}, configuration)
    champs = {champ: sortie["etapes"][0][champ] for champ in (
        "objectif", "action", "resultat_attendu", "verification", "retour_arriere"
    )}
    champs["action"] = "Sauvegarde précisée par l’utilisateur."
    reprendre_guide(graphe, configuration, {"action": "reformuler", "champs": champs})
    reprendre_guide(graphe, configuration, {"action": "confirmer"})
    reprendre_guide(graphe, configuration, {"action": "sauter"})
    etat = graphe.get_state(configuration).values
    avant = deepcopy(etat)
    texte = generer_compte_rendu("Atlas", ["demo.py"], [], guide=etat)
    assert "État du parcours : en cours." in texte
    assert "Étapes confirmées : 1 ; étapes sautées : 1." in texte
    assert "Étape 1 — Préparer — confirmée" in texte
    assert "Étape 2 — Modifier — sautée" in texte
    assert "Étape 3 — Vérifier — à faire" in texte
    assert champs["action"] in texte
    assert "confirmations ne constituent pas une vérification automatique" in texte
    assert etat == avant
    assert graphe.get_state(configuration).values == avant


@pytest.mark.parametrize("statut,libelle", [
    ("parcouru", "parcours terminé"), ("abandonne", "abandonné"),
])
def test_export_statut_terminal_du_plan_honnete(statut, libelle):
    proposition = donnees_recommandations()["recommandations"][0]
    graphe = build_guided_plan_graph([Chunk("demo.py", 1, 2, "a = 1\nb = 2")], proposition)
    configuration = {"configurable": {"thread_id": statut}}
    graphe.invoke({}, configuration)
    actions = ["sauter"] * 3 if statut == "parcouru" else ["abandonner"]
    for action in actions:
        reprendre_guide(graphe, configuration, {"action": action})
    texte = generer_compte_rendu("Atlas", [], [], guide=graphe.get_state(configuration).values)
    assert f"État du parcours : {libelle}." in texte
    assert "Étapes confirmées : 0" in texte


@pytest.fixture
def interface_export(monkeypatch):
    uploads = []
    telechargements = []
    model = RecordingModel()
    embeddings = CountingEmbeddings()
    monkeypatch.setattr(st, "file_uploader", lambda *a, **kw: list(uploads))
    monkeypatch.setattr(config, "load_settings", lambda: Settings("local", "modele-test"))
    monkeypatch.setattr(ollama_client, "create_chat_model", lambda settings: model)
    monkeypatch.setattr(encoders, "TransformerEmbeddings", lambda encoder: embeddings)

    def download(label, **kwargs):
        # Les exports d’impact et d’évaluation sont indépendants du compte rendu testé.
        if kwargs.get('key') == 'telecharger_compte_rendu':
            telechargements.append({"label": label, **kwargs})
        return False

    monkeypatch.setattr(st, "download_button", download)
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"))
    return app, uploads, model, telechargements


def contenu(telechargements):
    return telechargements[-1]["data"].decode("utf-8-sig")


def test_bouton_export_desactive_au_demarrage_et_actif_avec_nom(interface_export):
    app, _, model, telechargements = interface_export
    app.run(timeout=15)
    assert not app.exception
    assert telechargements[-1]["disabled"]
    app.text_input[0].set_value("Atlas").run(timeout=15)
    assert not app.exception
    assert not telechargements[-1]["disabled"]
    assert telechargements[-1]["file_name"] == "compte_rendu_revisiter.md"
    assert telechargements[-1]["mime"] == "text/markdown; charset=utf-8"
    assert telechargements[-1]["on_click"] == "ignore"
    assert not model.calls


def test_export_derniere_reponse_et_suppression_discussion_sans_recalcul(interface_export):
    app, uploads, model, telechargements = interface_export
    uploads.append(Upload("demo.py", b"# cible_panier\na = 1"))
    app.run(timeout=15)
    app.button(key="preparer_vue").click().run(timeout=15)
    app.button(key="proposer_ameliorations").click().run(timeout=15)
    app.chat_input[0].set_value("QUESTION_LA_PLUS_RECENTE").run(timeout=15)
    assert not app.exception
    texte = contenu(telechargements)
    assert "QUESTION_LA_PLUS_RECENTE" in texte
    assert app.session_state["messages"][-1]["content"] in texte
    assert app.session_state["vue_ensemble"]["answer"] in texte
    assert len(model.calls) == 3
    app.button(key="effacer_discussion").click().run(timeout=15)
    assert "QUESTION_LA_PLUS_RECENTE" not in contenu(telechargements)
    assert "Aucun message dans la discussion actuelle." in contenu(telechargements)
    assert len(model.calls) == 3


def test_export_disponible_apres_panne_ollama_sans_question_orpheline(interface_export):
    app, uploads, model, telechargements = interface_export
    uploads.append(Upload("demo.py", b"# cible_panier\na = 1"))
    app.run(timeout=15)
    app.chat_input[0].set_value("QUESTION_VALIDE").run(timeout=15)
    avant = len(telechargements)
    model.fail = True
    app.chat_input[0].set_value("QUESTION_EN_ECHEC").run(timeout=15)
    assert not app.exception
    assert app.error
    assert len(telechargements) == avant + 1
    assert "QUESTION_VALIDE" in contenu(telechargements)
    assert "QUESTION_EN_ECHEC" not in contenu(telechargements)
    assert len(app.session_state["messages"]) == 2


@pytest.mark.parametrize("changement", ["nom", "contenu", "suppression_fichiers"])
def test_export_changement_de_projet_ne_melange_pas_les_anciens_resultats(interface_export, changement):
    app, uploads, _, telechargements = interface_export
    uploads.append(Upload("demo.py", b"# cible_panier\na = 1"))
    app.run(timeout=15)
    app.button(key="proposer_ameliorations").click().run(timeout=15)
    app.button(key="preparer_guide").click().run(timeout=15)
    app.chat_input[0].set_value("QUESTION_ANCIEN_PROJET").run(timeout=15)
    assert "QUESTION_ANCIEN_PROJET" in contenu(telechargements)
    if changement == "nom":
        app.text_input[0].set_value("Nouveau nom").run(timeout=15)
    else:
        if changement == "contenu":
            uploads[0] = Upload("demo.py", b"# nouvelle version\na = 2")
        else:
            uploads.clear()
        app.run(timeout=15)
    assert not app.exception
    texte = contenu(telechargements)
    assert "QUESTION_ANCIEN_PROJET" not in texte
    assert "Aucune analyse d’améliorations préparée." in texte
    assert "Aucun plan préparé." in texte


def test_export_progression_du_guide_apres_confirmation_et_retour(interface_export):
    app, uploads, model, telechargements = interface_export
    uploads.append(Upload("demo.py", b"# regle\na = 1"))
    app.run(timeout=15)
    app.button(key="proposer_ameliorations").click().run(timeout=15)
    app.button(key="preparer_guide").click().run(timeout=15)
    app.button(key="guide_confirmer").click().run(timeout=15)
    assert "Étape 1 — Préparer — confirmée" in contenu(telechargements)
    app.button(key="guide_sauter").click().run(timeout=15)
    assert "Étape 2 — Modifier — sautée" in contenu(telechargements)
    app.button(key="guide_retour").click().run(timeout=15)
    assert "Étape 2 — Modifier — à faire" in contenu(telechargements)
    assert len(model.calls) == 1
