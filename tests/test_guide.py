"""Vérifier les pauses, les décisions humaines et l'absence de progression implicite."""

from copy import deepcopy
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from revisiter import config, ollama_client
from revisiter.config import Settings
from revisiter.guide import (
    CHAMPS_TEXTE,
    ErreurGuide,
    build_guided_plan_graph,
    reprendre_guide,
    valider_etapes,
)
from revisiter.ingestion import Chunk


def recommandation(reference="demo.py:L1-L2"):
    return {
        "titre": "Vérifier le stock restant", "justification": "Le stock peut devenir négatif.",
        "proposition": "Refuser une quantité supérieure au stock disponible.",
        "benefice": "Conserver un stock positif ou nul.", "priorite": "haute", "effort": "faible",
        "references": [reference],
    }


def plan(reference="demo.py:L1-L2"):
    return {"etapes": [{
        "objectif": f"Objectif {i}", "action": f"Action manuelle {i}",
        "resultat_attendu": f"Résultat {i}", "verification": f"Vérification {i}",
        "retour_arriere": f"Annulation manuelle {i}", "references": [reference],
    } for i in range(1, 4)]}


class Model:
    def __init__(self):
        self.calls = []
        self.schemas = []
        self.fail = False

    def with_structured_output(self, schema, *, method):
        assert method == "json_schema"
        self.schemas.append(deepcopy(schema))
        parent = self

        class Structured:
            def invoke(self, messages):
                parent.calls.append(messages)
                if parent.fail:
                    raise ConnectionError("Erreur interne à ne pas afficher")
                return {"recommandations": [recommandation()]}

        return Structured()


def nouveau_guide(model=None, source="demo.py"):
    model = model or Model()
    graphe = build_guided_plan_graph(
        [Chunk(source, 1, 2, "# règle : stock non négatif\nstock = 5")],
        recommandation(f"{source}:L1-L2"),
    )
    configuration = {"configurable": {"thread_id": "test-guide"}}
    return graphe, configuration, model


def test_preparation_bornee_et_pause_sans_progression_ni_appel_au_modele():
    graphe, configuration, model = nouveau_guide()
    sortie = graphe.invoke({"project_name": "Atlas"}, configuration)
    assert sortie["position"] == 0
    assert sortie["statuts"] == ["a_faire"] * 3
    assert sortie["__interrupt__"]
    assert sortie["project_name"] == "Atlas"
    assert len(sortie["context"]) <= 6_000
    assert sortie["etapes"][0]["fichiers"] == ["demo.py"]
    assert graphe.get_state(configuration).next == ("attendre_decision",)
    assert not model.calls
    assert not model.schemas
    action = sortie["etapes"][1]["action"]
    assert recommandation()["proposition"] in action
    assert "Restaurer manuellement les seules lignes" in sortie["etapes"][1]["retour_arriere"]
    assert "AVANT l’essai" in sortie["etapes"][2]["action"]
    assert "cas normal" in sortie["etapes"][2]["action"]


@pytest.mark.parametrize("cas", ["vide", "deux", "quatre", "reference", "champ", "long", "type"])
def test_plan_incomplet_ou_source_inventee_rejete(cas):
    donnees = plan()
    if cas == "vide":
        donnees["etapes"][0]["verification"] = " "
    elif cas == "deux":
        donnees["etapes"].pop()
    elif cas == "quatre":
        donnees["etapes"].append(deepcopy(donnees["etapes"][0]))
    elif cas == "reference":
        donnees["etapes"][0]["references"] = ["autre.py:L1-L2"]
    elif cas == "champ":
        del donnees["etapes"][0]["retour_arriere"]
    elif cas == "long":
        donnees["etapes"][0]["action"] = "x" * 1_201
    else:
        donnees["etapes"][0]["references"] = "demo.py:L1-L2"
    with pytest.raises(ErreurGuide):
        valider_etapes(donnees, ["demo.py:L1-L2"])


def test_confirmer_sauter_revenir_reformuler_sans_nouvel_appel_au_modele():
    graphe, configuration, model = nouveau_guide()
    graphe.invoke({}, configuration)
    sortie = reprendre_guide(graphe, configuration, {"action": "confirmer"})
    assert sortie["position"] == 1
    assert sortie["statuts"] == ["confirmee", "a_faire", "a_faire"]
    sortie = reprendre_guide(graphe, configuration, {"action": "sauter"})
    assert sortie["position"] == 2
    assert sortie["statuts"] == ["confirmee", "sautee", "a_faire"]
    sortie = reprendre_guide(graphe, configuration, {"action": "retour"})
    assert sortie["position"] == 1
    assert sortie["statuts"] == ["confirmee", "a_faire", "a_faire"]
    champs = {champ: sortie["etapes"][1][champ] for champ in CHAMPS_TEXTE}
    champs["action"] = "Ma reformulation à réaliser manuellement."
    sortie = reprendre_guide(graphe, configuration, {"action": "reformuler", "champs": champs})
    assert sortie["etapes"][1]["action"] == champs["action"]
    assert sortie["etapes"][1]["references"] == ["demo.py:L1-L2"]
    assert sortie["position"] == 1
    reprendre_guide(graphe, configuration, {"action": "confirmer"})
    sortie = reprendre_guide(graphe, configuration, {"action": "sauter"})
    assert sortie["statut"] == "parcouru"
    assert sortie["statuts"] == ["confirmee", "confirmee", "sautee"]
    sortie = reprendre_guide(graphe, configuration, {"action": "retour"})
    assert sortie["position"] == 2
    assert sortie["statut"] == "en_cours"
    assert len(model.calls) == 0


@pytest.mark.parametrize("decision", [
    {"action": "inconnue"}, {"action": "retour"},
    {"action": "reformuler", "champs": {"action": "Pas tous les champs"}},
])
def test_decision_invalide_ne_consomme_pas_la_pause(decision):
    graphe, configuration, _ = nouveau_guide()
    graphe.invoke({}, configuration)
    avant = graphe.get_state(configuration)
    with pytest.raises(ErreurGuide):
        reprendre_guide(graphe, configuration, decision)
    apres = graphe.get_state(configuration)
    assert apres.values == avant.values
    assert apres.next == avant.next
    assert reprendre_guide(graphe, configuration, {"action": "confirmer"})["position"] == 1


def test_abandon_met_fin_aux_decisions_et_garde_statuts_honnetes():
    graphe, configuration, model = nouveau_guide()
    graphe.invoke({}, configuration)
    sortie = reprendre_guide(graphe, configuration, {"action": "abandonner"})
    assert sortie["statut"] == "abandonne"
    assert sortie["statuts"] == ["a_faire"] * 3
    assert not graphe.get_state(configuration).next
    with pytest.raises(ErreurGuide):
        reprendre_guide(graphe, configuration, {"action": "confirmer"})
    assert len(model.calls) == 0


def test_etats_de_deux_parcours_isoles_et_notebook_compte_comme_fichier():
    graphe, config1, _ = nouveau_guide(source="essai.ipynb#cellule4")
    config2 = {"configurable": {"thread_id": "autre-guide"}}
    sortie = graphe.invoke({}, config1)
    assert sortie["etapes"][0]["fichiers"] == ["essai.ipynb"]
    graphe.invoke({}, config2)
    reprendre_guide(graphe, config1, {"action": "confirmer"})
    assert graphe.get_state(config1).values["position"] == 1
    assert graphe.get_state(config2).values["position"] == 0


def test_seules_les_sources_du_choix_sont_envoyees_et_le_code_reste_du_texte(tmp_path):
    fichier = tmp_path / "demo.py"
    texte = "raise RuntimeError('NE PAS EXECUTER')\n# règle"
    fichier.write_text(texte, encoding="utf-8")
    graph = build_guided_plan_graph([
        Chunk("autre.py", 1, 1, "DONNEE_HORS_CHOIX"),
        Chunk("demo.py", 1, 2, fichier.read_text(encoding="utf-8")),
    ], recommandation("demo.py:L2-L2"))
    sortie = graph.invoke({}, {"configurable": {"thread_id": "texte-seul"}})
    assert len(sortie["context"]) <= 6_000
    assert sortie["sources"] == ["demo.py:L1-L2"]
    assert "DONNEE_HORS_CHOIX" not in sortie["context"]
    assert fichier.read_text(encoding="utf-8") == texte


def test_contexte_incomplet_rejete_avant_preparation():
    model = Model()
    with pytest.raises((ErreurGuide, ValueError)):
        build_guided_plan_graph([
            Chunk("demo.py", 1, 1, "a"), Chunk("demo.py", 2, 2, "b" * 300)
        ], recommandation(), max_context_chars=40)
    assert not model.calls


class Upload:
    name = "demo.py"

    def __init__(self, donnees=b"# regle\nstock = 5"):
        self.donnees = donnees
        self.size = len(donnees)

    def getvalue(self):
        return self.donnees


@pytest.fixture
def interface_guide(monkeypatch):
    uploads = [Upload()]
    model = Model()
    monkeypatch.setattr(st, "file_uploader", lambda *a, **kw: list(uploads))
    monkeypatch.setattr(config, "load_settings", lambda: Settings("local", "modele-test"))
    monkeypatch.setattr(ollama_client, "create_chat_model", lambda settings: model)
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"))
    app.run(timeout=15)
    app.button(key="proposer_ameliorations").click().run(timeout=15)
    assert not app.exception
    return app, uploads, model


def test_interface_un_seul_pas_confirme_saute_retour_reformule_et_abandon(interface_guide):
    app, _, model = interface_guide
    app.button(key="preparer_guide").click().run(timeout=15)
    assert not app.exception
    assert not app.error
    assert any("Étape 1 / 3" in m.value for m in app.markdown)
    assert not any("Étape 2 / 3" in m.value for m in app.markdown)
    assert app.button(key="guide_retour").disabled
    guide = app.session_state["guide"]
    assert guide["graphe"].get_state(guide["config"]).values["position"] == 0
    app.run(timeout=15)
    assert len(model.calls) == 1  # Les recommandations seules appellent le modèle.
    app.button(key="guide_confirmer").click().run(timeout=15)
    assert any("Étape 2 / 3" in m.value for m in app.markdown)
    app.button(key="guide_sauter").click().run(timeout=15)
    assert any("Étape 3 / 3" in m.value for m in app.markdown)
    app.button(key="guide_retour").click().run(timeout=15)
    assert any("Étape 2 / 3" in m.value for m in app.markdown)
    champ_action = next(t for t in app.text_area if t.label == "Action manuelle")
    champ_action.set_value("Action reformulée dans le formulaire.")
    next(b for b in app.button if b.label == "Enregistrer la reformulation").click().run(timeout=15)
    assert not app.exception
    guide = app.session_state["guide"]
    assert guide["graphe"].get_state(guide["config"]).values["etapes"][1]["action"] == (
        "Action reformulée dans le formulaire."
    )
    app.button(key="guide_abandonner").click().run(timeout=15)
    assert any("Plan abandonné" in info.value for info in app.info)
    assert len(model.calls) == 1
    assert app.session_state["messages"] == []


def test_interface_parcours_termine_distingue_etape_sautee_et_confirmation(interface_guide):
    app, _, _ = interface_guide
    app.button(key="preparer_guide").click().run(timeout=15)
    app.button(key="guide_confirmer").click().run(timeout=15)
    app.button(key="guide_sauter").click().run(timeout=15)
    app.button(key="guide_confirmer").click().run(timeout=15)
    assert not app.exception
    assert any("2 étape(s) confirmée(s), 1 sautée(s)" in m.value for m in app.success)
    app.button(key="guide_retour_final").click().run(timeout=15)
    assert any("Étape 3 / 3" in m.value for m in app.markdown)


@pytest.mark.parametrize("changement", ["nom", "contenu", "nouvelle_analyse"])
def test_interface_changer_de_projet_ou_analyse_supprime_le_guide(interface_guide, changement):
    app, uploads, _ = interface_guide
    app.button(key="preparer_guide").click().run(timeout=15)
    if changement == "nom":
        app.text_input[0].set_value("Autre projet").run(timeout=15)
    elif changement == "contenu":
        uploads[0] = Upload(b"# nouvelle regle\nstock = 8")
        app.run(timeout=15)
    else:
        app.button(key="proposer_ameliorations").click().run(timeout=15)
    assert not app.exception
    assert "guide" not in app.session_state


def test_interface_preparation_du_plan_sans_ollama_apres_les_recommandations(interface_guide):
    app, _, model = interface_guide
    model.fail = True
    app.button(key="preparer_guide").click().run(timeout=15)
    assert not app.exception
    assert not app.error
    assert "guide" in app.session_state
    assert len(model.calls) == 1


def test_proposition_longue_et_benefice_long_restent_dans_les_limites():
    proposition = recommandation()
    proposition["proposition"] = "c" * 800
    proposition["benefice"] = "b" * 500
    graph = build_guided_plan_graph([Chunk("demo.py", 1, 2, "stock = 5\n# règle")], proposition)
    sortie = graph.invoke({}, {"configurable": {"thread_id": "champs-longs"}})
    assert proposition["proposition"] in sortie["etapes"][1]["action"]
    assert proposition["benefice"] in sortie["etapes"][1]["resultat_attendu"]


def test_changement_et_reformulation_ne_mutent_pas_la_proposition_initiale():
    proposition = recommandation()
    initiale = deepcopy(proposition)
    graphe = build_guided_plan_graph([Chunk("demo.py", 1, 2, "stock = 5\n# règle")], proposition)
    configuration = {"configurable": {"thread_id": "copie-choix"}}
    sortie = graphe.invoke({}, configuration)
    champs = {champ: sortie["etapes"][0][champ] for champ in CHAMPS_TEXTE}
    champs["action"] = "Action réécrite par le développeur."
    reprendre_guide(graphe, configuration, {"action": "reformuler", "champs": champs})
    assert proposition == initiale
