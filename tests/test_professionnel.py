"""Cas de code multilangage, recherche paresseuse et contrôles de réponse."""

from pathlib import Path

import pytest
import streamlit as st
from langchain_core.messages import AIMessageChunk
from streamlit.testing.v1 import AppTest
from test_guide import recommandation
from test_rapidite import SansEncodeur
from test_recherche import CountingEmbeddings, RecordingModel, Upload

from revisiter import config, encoders, ollama_client, workflow
from revisiter.config import Settings
from revisiter.guide import build_guided_plan_graph, reprendre_guide
from revisiter.ingestion import Chunk
from revisiter.recherche_code import IndexCode
from revisiter.robustesse import ErreurReponse, controler_citations
from revisiter.structures import analyser_structure
from revisiter.workflow import build_question_graph, iterer_reponse


@pytest.mark.parametrize(("nom", "code", "attendu"), [
    ("a.py", "def total(x):\n    return x + 1\n", "total"),
    ("a.js", "export function total(x) { return x + 1; }", "total"),
    ("a.ts", "export const total = (x: number): number => x + 1;", "total"),
    ("a.java", "class A { int total(int x) { return x + 1; } }", "total"),
    ("a.cs", "class A { int Total(int x) { return x + 1; } }", "Total"),
    ("a.go", "package main\nfunc total(x int) int { return x + 1 }", "total"),
    ("a.rs", "fn total(x: i32) -> i32 { x + 1 }", "total"),
    ("a.cpp", "int total(int x) { return x + 1; }", "total"),
    ("a.php", "<?php function total($x) { return $x + 1; }", "total"),
])
def test_parseurs_renvoient_des_declarations_reelles(nom, code, attendu):
    analyse = analyser_structure(nom, code)
    assert analyse["status"] == "ok"
    assert attendu in [s["name"] for s in analyse["symbols"] if s["kind"] == "function"]
    assert all(s["reference"].startswith(nom + ":L") for s in analyse["symbols"])


def test_analyser_ne_lance_ni_import_ni_appel(tmp_path):
    marque = tmp_path / "ne_pas_creer.txt"
    code = f"from pathlib import Path\nPath({str(marque)!r}).write_text('EXECUTE')\n"
    assert analyser_structure("a.py", code)["status"] == "ok"
    assert not marque.exists()


def test_inventaire_et_appels_multifichiers_sans_modele_ni_nlp():
    documents = {"calcul.py": "def total_panier(prix):\n    return sum(prix)\n",
                 "service.py": "from calcul import total_panier\n# total_panier() n’est qu’un commentaire\ndef commander(prix):\n    return total_panier(prix)\n",
                 "formater.ts": "export function afficher(x: number) { return String(x); }"}
    chunks = [Chunk(n, 1, len(c.splitlines()), c) for n, c in documents.items()]
    modele = RecordingModel()
    graphe = build_question_graph(chunks, SansEncodeur(), modele, retrieval_mode="auto", documents=documents)
    inventaire = graphe.invoke({"question": "Liste les fonctions et méthodes."})
    assert "afficher" in inventaire["answer"] and "commander" in inventaire["answer"]
    appels = graphe.invoke({"question": "Quel fichier appelle total_panier ?"})
    assert appels["sources"] == ["service.py:L4-L4"]
    assert appels["metrics"]["method"] == "static"
    assert not modele.calls


def test_appels_respectent_la_casse_du_nom_demande():
    code = 'def f():\n    Foo()\n    foo()\n'
    index = IndexCode([Chunk("x.py", 1, 3, code)], SansEncodeur(), {"x.py": code})
    assert index.rechercher("Quel fichier appelle Foo ?", 4, 6000)["sources"] == ["x.py:L2-L2"]


def test_export_contient_observations_contrat_diff_et_empreinte():
    from test_transformations import CODE, proposition

    from revisiter.export import generer_compte_rendu
    from revisiter.transformations import valider_transformation

    graph = build_guided_plan_graph([Chunk("demo.py", 1, 2, "a = 1\nb = 2")], recommandation())
    conf = {"configurable": {"thread_id": "export-observations"}}
    graph.invoke({}, conf)
    etat = reprendre_guide(graph, conf, {"action": "confirmer", "observation": "Sauvegarde comparée."})
    transformation = valider_transformation(proposition(), "x.py", CODE, ["x.py:L1-L3"])
    transformation["contract"] = {"objectif": "Stock positif", "contraintes": "API conservée", "criteres": "Stock inchangé après refus"}
    texte = generer_compte_rendu("Atlas", ["x.py"], [], guide=etat, transformation=transformation)
    assert "Sauvegarde comparée." in texte and "Stock inchangé après refus" in texte
    assert transformation["base_sha256"] in texte and transformation["diff"] in texte


def test_liste_des_risques_reste_une_question_au_modele():
    modele = RecordingModel()
    graphe = build_question_graph([Chunk("x.py", 1, 2, "def f():\n    return 1")], SansEncodeur(), modele,
        retrieval_mode="auto", documents={"x.py": "def f():\n    return 1"})
    graphe.invoke({"question": "Liste les risques de ces fonctions."})
    assert len(modele.calls) == 1


def test_ancre_de_code_ecarte_les_correspondances_partielles():
    chunks = [Chunk("a.py", 1, 1, "def total_panier(prix): return sum(prix)"),
              Chunk("b.py", 1, 1, "return total_panier(prix)"),
              Chunk("autre.ts", 1, 1, "function total() { return 123; }")]
    resultat = IndexCode(chunks, SansEncodeur()).rechercher("Explique total_panier.", 4, 6000)
    assert set(resultat["sources"]) == {"a.py:L1-L1", "b.py:L1-L1"}
    assert resultat["metrics"]["method"] == "lexical"


def test_cache_hybride_ne_recalcule_pas_les_vecteurs_des_documents():
    encodeur = CountingEmbeddings()
    index = IndexCode([Chunk(f"f{i}.py", 1, 1, "documentation " * 100) for i in range(8)], encodeur)
    index.rechercher("Comment comprendre les responsabilités ?", 4, 6000)
    index.rechercher("Quel est le comportement métier ?", 4, 6000)
    assert len(encodeur.document_calls) == 1 and len(encodeur.query_calls) == 2


def test_nlp_indisponible_fallback_sans_nouvel_essai_pour_chaque_question():
    class Echec(CountingEmbeddings):
        def embed_documents(self, texts):
            self.document_calls.append(texts)
            raise OSError("Ne pas afficher ce détail privé")
    encodeur = Echec()
    index = IndexCode([Chunk(f"f{i}.py", 1, 1, "documentation " * 100) for i in range(8)], encodeur)
    for question in ("Quelle architecture ?", "Quel fonctionnement ?"):
        resultat = index.rechercher(question, 4, 6000)
        assert resultat["sources"]
        assert "lexicale" in resultat["metrics"]["warning"]
        assert "privé" not in resultat["metrics"]["warning"]
    assert len(encodeur.document_calls) == 1


@pytest.mark.parametrize("citation", ["[x.py:L4]", "[x.py:L1-L4]", "[autre.py:L1]", "[x.py:L3-L2]"])
def test_citations_inventees_ou_hors_plage_rejetees(citation):
    with pytest.raises(ErreurReponse):
        controler_citations("Constat " + citation, ["x.py:L1-L3"])


def test_citation_precise_simple_et_plages_adjacentes():
    assert controler_citations("Voir [x.py:L2] et [x.py:L1-L3]", ["x.py:L1-L2", "x.py:L3-L3"]) == "checked"
    assert controler_citations("Sans citation", ["x.py:L1-L3"]) == "missing"


@pytest.mark.parametrize("raison", ["length", "deadline"])
def test_interruption_ferme_le_flux_et_ne_valide_pas_la_reponse(monkeypatch, raison):
    horloge = [0.0]
    monkeypatch.setattr(workflow, "perf_counter", lambda: horloge[0])
    ferme = []
    class Modele:
        def stream(self, messages):
            try:
                yield AIMessageChunk(content="Début ")
                if raison == "deadline":
                    horloge[0] = 121
                yield AIMessageChunk(content="suite", response_metadata={"done_reason": "length" if raison == "length" else "stop"})
            finally:
                ferme.append(True)
    graphe = build_question_graph([], SansEncodeur(), Modele())
    resultat = {}
    flux = iterer_reponse(graphe, {"question": "Bonjour"}, resultat)
    assert next(flux) == "Début "
    with pytest.raises(ErreurReponse):
        list(flux)
    assert ferme and "answer" not in resultat


def test_observations_suivent_les_etapes_et_sont_effacees_au_retour():
    graphe = build_guided_plan_graph([Chunk("demo.py", 1, 2, "a = 1\nb = 2")], recommandation())
    conf = {"configurable": {"thread_id": "observations"}}
    graphe.invoke({}, conf)
    etat = reprendre_guide(graphe, conf, {"action": "confirmer", "observation": "Copie vérifiée par comparaison."})
    assert etat["observations"][0] == "Copie vérifiée par comparaison."
    etat = reprendre_guide(graphe, conf, {"action": "retour"})
    assert etat["observations"] == ["", "", ""]


def test_echec_d_une_nouvelle_analyse_conserve_la_derniere_reussite(monkeypatch):
    modele = RecordingModel()
    monkeypatch.setattr(st, "file_uploader", lambda *a, **kw: [Upload("demo.py", b"a = 1\nb = 2")])
    monkeypatch.setattr(config, "load_settings", lambda: Settings("local", "modele-test"))
    monkeypatch.setattr(encoders, "TransformerEmbeddings", lambda encoder: SansEncodeur())
    monkeypatch.setattr(ollama_client, "create_chat_model", lambda settings, **kw: modele)
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py")).run(timeout=15)
    app.button(key="preparer_vue").click().run(timeout=15)
    avant = dict(app.session_state["vue_ensemble"])
    modele.fail = True
    app.button(key="preparer_vue").click().run(timeout=15)
    assert not app.exception and app.error
    assert app.session_state["vue_ensemble"] == avant


def test_parcours_interface_prepare_un_diff_et_conserve_la_derniere_proposition_si_echec(monkeypatch):
    from test_transformations import CODE, remplacement

    from revisiter import ui_professionnelle

    modele = RecordingModel()
    monkeypatch.setattr(st, "file_uploader", lambda *a, **kw: [Upload("demo.py", CODE.encode())])
    monkeypatch.setattr(config, "load_settings", lambda: Settings("local", "modele-test"))
    monkeypatch.setattr(encoders, "TransformerEmbeddings", lambda encoder: SansEncodeur())
    monkeypatch.setattr(ollama_client, "create_chat_model", lambda settings, **kw: modele)
    monkeypatch.setattr(ui_professionnelle, "create_chat_model", lambda settings, **kw: modele)
    monkeypatch.setattr(ui_professionnelle, "load_settings", lambda: Settings("local", "modele-test"))
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py")).run(timeout=15)
    app.button(key="proposer_ameliorations").click().run(timeout=15)
    app.button(key="preparer_guide").click().run(timeout=15)
    modele.structured_result = remplacement()
    bouton = next(b for b in app.button if b.label == "Préparer le diff et les cas de vérification")
    bouton.click().run(timeout=15)
    assert not app.exception and "transformation" in app.session_state
    avant = dict(app.session_state["transformation"])
    assert "Stock insuffisant" in avant["diff"] and avant["syntax"] == "ok"
    assert any(c.language == "diff" for c in app.code)
    modele.fail = True
    next(b for b in app.button if b.label == "Préparer le diff et les cas de vérification").click().run(timeout=15)
    assert not app.exception and app.error
    assert dict(app.session_state["transformation"]) == avant
