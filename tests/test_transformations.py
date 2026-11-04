"""Contrôler des propositions de code sans lancer le fichier importé."""

from copy import deepcopy

import pytest
from test_recherche import RecordingModel

from revisiter.transformations import (
    ErreurTransformation,
    empreinte,
    portees_transformation,
    proposer_transformation,
    valider_transformation,
)

CODE = 'def retirer(stock, quantite):\n    stock["stylo"] -= quantite\n    return stock["stylo"]\n'


def proposition():
    return {"title": "Préserver le stock", "summary": "Vérifier la quantité avant la mutation.",
        "edits": [{"before": '    stock["stylo"] -= quantite',
                   "after": '    if quantite > stock["stylo"]:\n        raise ValueError("Stock insuffisant")\n    stock["stylo"] -= quantite',
                   "reason": "Le retrait ne doit pas rendre le stock négatif.", "references": ["x.py:L1-L3"]}],
        "tests": ["Stock 5, retirer 2 : résultat 3.", "Stock 5, retirer 6 : ValueError et stock encore égal à 5."]}


def remplacement():
    brut = proposition()
    edition = brut["edits"][0]
    return {"replacement": CODE.replace(edition["before"], edition["after"]), "tests": brut["tests"]}


def test_diff_hash_syntaxe_signatures_et_fichier_original_inchange():
    resultat = valider_transformation(proposition(), "x.py", CODE, ["x.py:L1-L3"])
    assert resultat["base_sha256"] == empreinte(CODE)
    assert resultat["syntax"] == "ok" and resultat["executed"] is False
    assert "conservés" in resultat["api"]
    assert '+        raise ValueError("Stock insuffisant")' in resultat["diff"]
    assert "Stock insuffisant" not in CODE
    assert resultat["code"].index("raise ValueError") < resultat["code"].index('-= quantite')


@pytest.mark.parametrize("nom", ["../x.py", "dossier/x.py", "dossier\\x.py", "x.py\n--- a", "x.ipynb"])
def test_nom_ou_format_ne_peut_pas_transformer_un_autre_chemin(nom):
    with pytest.raises(ErreurTransformation):
        valider_transformation(proposition(), nom, CODE, ["x.py:L1-L3"])


@pytest.mark.parametrize("cas", ["introuvable", "duplique", "hors_sources", "hors_lignes", "syntax", "signature", "chevauchement"])
def test_rejets_de_propositions_dangereuses_ou_non_applicables(cas):
    brut = proposition()
    code = CODE
    if cas == "introuvable":
        brut["edits"][0]["before"] = "code absent"
    elif cas == "duplique":
        code += '    stock["stylo"] -= quantite\n'
    elif cas == "hors_sources":
        brut["edits"][0]["references"] = ["autre.py:L1-L3"]
    elif cas == "hors_lignes":
        brut["edits"][0]["references"] = ["x.py:L3-L3"]
    elif cas == "syntax":
        brut["edits"][0]["after"] = "    if"
    elif cas == "signature":
        brut["edits"] = [{"before": "def retirer(stock, quantite):", "after": "def retirer(stock):",
                           "reason": "Changer les paramètres", "references": ["x.py:L1-L3"]}]
    else:
        brut["edits"].append(deepcopy(brut["edits"][0]))
    with pytest.raises(ErreurTransformation):
        valider_transformation(brut, "x.py", code, ["x.py:L1-L3"])


def test_changement_de_parametres_demande_explicitement():
    brut = proposition()
    brut["edits"] = [{"before": "def retirer(stock, quantite):", "after": "def retirer(stock, quantite=1):",
                       "reason": "Valeur par défaut demandée", "references": ["x.py:L1-L3"]}]
    assert valider_transformation(brut, "x.py", CODE, ["x.py:L1-L3"], autoriser_api=True)["syntax"] == "ok"


def test_fins_de_ligne_windows_conservees():
    original = CODE.replace("\n", "\r\n")
    resultat = valider_transformation(proposition(), "x.py", original, ["x.py:L1-L3"])
    assert resultat["base_sha256"] == empreinte(original)
    assert "\r\n" in resultat["code"]
    assert "\n" not in resultat["code"].replace("\r\n", "")


def test_formatage_des_parametres_ne_compte_pas_comme_un_changement_d_api():
    code = 'function f(x = "a b") { return x; }'
    brut = {"title": "Formatage", "summary": "Espaces", "tests": ["Résultat inchangé"],
        "edits": [{"before": code, "after": 'function f( x = "a b" ) { return x; }',
                   "reason": "Formatage", "references": ["x.js:L1-L1"]}]}
    assert valider_transformation(brut, "x.js", code, ["x.js:L1-L1"])["syntax"] == "ok"


def test_analyse_du_code_modifie_n_execute_pas_les_appels_globaux(tmp_path):
    marque = tmp_path / "jamais_cree"
    code = f"from pathlib import Path\nPath({str(marque)!r}).write_text('INTERDIT')\ndef f():\n    return 1\n"
    brut = {"title": "Modifier le résultat", "summary": "Proposition locale", "tests": ["f retourne 2"],
        "edits": [{"before": "    return 1", "after": "    return 2", "reason": "Objectif demandé", "references": ["x.py:L4-L4"]}]}
    assert valider_transformation(brut, "x.py", code, ["x.py:L1-L4"])["syntax"] == "ok"
    assert not marque.exists()


def test_generation_structuree_une_fois_et_contrat_conserve():
    modele = RecordingModel()
    modele.structured_result = remplacement()
    resultat = proposer_transformation(modele, "x.py", CODE, ["x.py:L1-L3"],
        "Empêcher le dépassement du stock", "Conserver les paramètres", "Erreur avant mutation si quantité supérieure au stock")
    assert len(modele.calls) == 1
    assert modele.structured_method == "json_schema"
    assert "avant mutation" in resultat["contract"]["criteres"]


def test_fichier_trop_long_refuse_avant_tout_appel():
    modele = RecordingModel()
    with pytest.raises(ErreurTransformation):
        proposer_transformation(modele, "x.py", "a" * 6001, ["x.py:L1-L1"], "Objectif", "Contraintes", "Critères")
    assert not modele.calls


def test_grand_fichier_cible_sur_les_passages_sans_envoyer_le_reste():
    modele = RecordingModel()
    modele.structured_result = remplacement()
    code = CODE + "# HORS_SCOPE " + "x" * 8000 + "\n"
    resultat = proposer_transformation(modele, "x.py", code, ["x.py:L1-L3"], "Préserver le stock", "Conserver l’API", "Erreur avant mutation")
    assert "HORS_SCOPE" not in modele.calls[0][-1].content
    assert "HORS_SCOPE" in resultat["code"]


def test_portee_etendue_a_la_fonction_complete_avec_selection_explicite():
    choix = portees_transformation("x.py", CODE, ["x.py:L2-L2"])
    assert choix[0]["reference"] == "x.py:L1-L3"
    assert choix[1]["reference"] == "x.py:L2-L2"


def test_portees_discontinues_refusees_avant_generation():
    modele = RecordingModel()
    with pytest.raises(ErreurTransformation, match="portion continue"):
        proposer_transformation(modele, "x.py", CODE, ["x.py:L1-L1", "x.py:L3-L3"], "Objectif", "Contraintes", "Critères")
    assert not modele.calls


def test_generation_tronquee_rejetee_meme_si_json_lisible():
    from langchain_core.messages import AIMessage

    class Modele:
        def with_structured_output(self, schema, *, method, include_raw=False):
            return self

        def invoke(self, messages):
            return {"parsed": remplacement(), "parsing_error": None,
                "raw": AIMessage(content="", response_metadata={"done_reason": "length"})}

    with pytest.raises(ErreurTransformation, match="interrompue"):
        proposer_transformation(Modele(), "x.py", CODE, ["x.py:L1-L3"], "Objectif", "Contraintes", "Critères")


def test_delai_du_flux_de_code_ferme_la_generation(monkeypatch):
    from langchain_core.messages import AIMessageChunk

    from revisiter import transformations

    horloge, ferme = [0], []
    monkeypatch.setattr(transformations, "perf_counter", lambda: horloge[0])

    class Modele:
        def bind(self, **kwargs):
            return self

        def stream(self, messages):
            try:
                yield AIMessageChunk(content='{')
                horloge[0] = 121
                yield AIMessageChunk(content='}')
            finally:
                ferme.append(True)

    with pytest.raises(ErreurTransformation, match="délai"):
        proposer_transformation(Modele(), "x.py", CODE, ["x.py:L1-L3"], "Objectif", "Contraintes", "Critères")
    assert ferme


@pytest.mark.parametrize("balises", [True, False])
def test_flux_de_code_ancre_par_le_programme_et_criteres_utilisateur_preserves(balises):
    from langchain_core.messages import AIMessageChunk

    code = remplacement()["replacement"]

    class Modele:
        def bind(self, **kwargs):
            assert not kwargs  # Pas de grammaire JSON pour générer du code source.
            return self

        def stream(self, messages):
            assert "ONLY" in messages[0].content
            yield AIMessageChunk(content="```python\n" + code + "\n```" if balises else code)
            yield AIMessageChunk(content="", response_metadata={"done_reason": "stop"})

    resultat = proposer_transformation(Modele(), "x.py", CODE, ["x.py:L1-L3"], "Objectif", "Contraintes", "Erreur avant mutation")
    assert resultat["tests"] == ["Erreur avant mutation"]
    assert resultat["tests_origin"] == "user_criteria" and resultat["syntax"] == "ok"


@pytest.mark.parametrize(("nom", "avant", "apres"), [
    ("a.js", "function f(x) { return x; }", "function f(x) { return ;"),
    ("A.java", "class A { int f(int x) { return x; } }", "class A { int f(int x) { return ;"),
    ("a.rs", "fn f(x: i32) -> i32 { x }", "fn f(x: i32) -> i32 {"),
])
def test_syntaxe_invalide_rejetee_aussi_hors_python(nom, avant, apres):
    brut = {"title": "Changement", "summary": "Essai", "tests": ["À vérifier"],
        "edits": [{"before": avant, "after": apres, "reason": "Test", "references": [f"{nom}:L1-L1"]}]}
    with pytest.raises(ErreurTransformation, match="syntaxe"):
        valider_transformation(brut, nom, avant, [f"{nom}:L1-L1"])
