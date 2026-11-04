"""Contrôler les noms des fichiers réellement représentés dans le contexte."""

import json

from test_recherche import CountingEmbeddings, RecordingModel

from revisiter.ingestion import Chunk
from revisiter.workflow import build_question_graph


def fichiers_du_message(message):
    titre = "FICHIERS AJOUTÉS DANS L’INTERFACE ET REPRÉSENTÉS DANS LES EXTRAITS\n"
    return json.loads(message.split(titre, 1)[1].split("\n\n", 1)[0])


def test_noms_des_fichiers_transmis_correspondent_a_la_selection_pas_au_projet_entier():
    modele = RecordingModel()
    graphe = build_question_graph([
        Chunk("demo.py", 1, 2, "# cible_panier\nvaleur = 1"),
        Chunk("non_selectionne.py", 1, 1, "autre = 2"),
    ], CountingEmbeddings(), modele, top_k=1)
    resultat = graphe.invoke({"question": "Quel est le rôle des fichiers importés ?"})
    message = modele.calls[0][-1].content
    assert fichiers_du_message(message) == ["demo.py"]
    assert resultat["context"] in message
    assert "non_selectionne.py" not in message


def test_cellules_notebook_regroupees_et_absence_de_fichiers_representee_honnetement():
    modele = RecordingModel()
    graphe = build_question_graph([
        Chunk("essai.ipynb#cellule1", 1, 1, "# cible_panier"),
        Chunk("essai.ipynb#cellule3", 1, 1, "total = 5"),
    ], CountingEmbeddings(), modele)
    resultat = graphe.invoke({"question": "Explique le notebook."})
    assert fichiers_du_message(modele.calls[-1][-1].content) == ["essai.ipynb"]
    assert len(resultat["sources"]) == 2
    sans_fichier = build_question_graph([], CountingEmbeddings(), modele)
    sans_fichier.invoke({"question": "Bonjour"})
    assert fichiers_du_message(modele.calls[-1][-1].content) == []
    assert "Aucun extrait fourni." in modele.calls[-1][-1].content
