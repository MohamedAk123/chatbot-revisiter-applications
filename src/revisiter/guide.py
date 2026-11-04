"""Plan manuel sourcé, interrompu à chaque décision de l'utilisateur."""

import re
from copy import deepcopy
from typing import Literal, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from revisiter.ingestion import Chunk
from revisiter.recommandations import valider_recommandations
from revisiter.workflow import MAX_CONTEXT_CHARS, prepare_project_context

PHASES = ("Préparer", "Modifier", "Vérifier")
CHAMPS_TEXTE = {
    "objectif": 200,
    "action": 1_200,
    "resultat_attendu": 600,
    "verification": 1_000,
    "retour_arriere": 600,
}


class GuideState(TypedDict, total=False):
    project_name: str
    recommandation: dict
    context: str
    sources: list[str]
    etapes: list[dict]
    position: int
    statuts: list[str]
    statut: Literal["en_cours", "parcouru", "abandonne"]
    decision: dict
    observations: list[str]


class ErreurGuide(ValueError):
    """Message contrôlé affichable dans l'interface."""


def _texte(valeur, maximum):
    if not isinstance(valeur, str) or not valeur.strip() or len(valeur.strip()) > maximum:
        raise ErreurGuide("Un champ d’étape est vide, invalide ou trop long.")
    return valeur.strip()


def valider_etapes(resultat, sources):
    """Contrôler le format et les références ; la pertinence reste à examiner."""
    if not isinstance(resultat, dict) or set(resultat) != {"etapes"}:
        raise ErreurGuide("Le plan ne respecte pas le format demandé.")
    etapes = resultat["etapes"]
    if not isinstance(etapes, list) or len(etapes) != 3:
        raise ErreurGuide("Le plan doit contenir exactement trois étapes.")
    validees = []
    for etape in etapes:
        if not isinstance(etape, dict) or set(etape) != set(CHAMPS_TEXTE) | {"references"}:
            raise ErreurGuide("Les champs d’une étape sont incomplets ou inattendus.")
        copie = {champ: _texte(etape[champ], maxi) for champ, maxi in CHAMPS_TEXTE.items()}
        refs = etape["references"]
        if not isinstance(refs, list) or not 1 <= len(refs) <= 5:
            raise ErreurGuide("Chaque étape doit citer un à cinq extraits fournis.")
        references = []
        for ref in refs:
            if not isinstance(ref, str) or ref not in sources:
                raise ErreurGuide("Une étape cite un extrait absent du contexte du plan.")
            if ref not in references:
                references.append(ref)
        copie["references"] = references
        # Les noms sont calculés à partir des sources connues, pas inventés par le modèle.
        copie["fichiers"] = sorted({
            re.sub(r"#cellule\d+$", "", ref.rsplit(":L", 1)[0]) for ref in references
        })
        validees.append(copie)
    return validees


def _plage(reference):
    source, lignes = reference.rsplit(":L", 1)
    debut, fin = map(int, lignes.split("-L"))
    return source, debut, fin


def _contexte_cible(chunks, recommandation, maximum):
    """Fournir les passages qui recouvrent les références de la proposition choisie."""
    if maximum < 1 or not chunks:
        raise ErreurGuide("Des extraits et un budget de contexte positif sont nécessaires.")
    enveloppe = {"recommandations": [recommandation], "limites": "Sélection à examiner."}
    recommandation = valider_recommandations(
        enveloppe, [chunk.reference for chunk in chunks]
    )["recommandations"][0]
    plages = [_plage(ref) for ref in recommandation["references"]]
    selection = [chunk for chunk in chunks if any(
        chunk.source == source and chunk.start_line <= fin and chunk.end_line >= debut
        for source, debut, fin in plages
    )]
    contexte = prepare_project_context(selection, maximum)
    try:
        valider_recommandations(enveloppe, contexte["sources"])
    except ValueError as erreur:
        raise ErreurGuide(
            "Les passages cités ne tiennent pas dans le contexte du plan. "
            "Choisis une proposition portant sur moins de passages."
        ) from erreur
    return recommandation, contexte


def appliquer_decision(state, decision):
    """Mettre à jour la progression déclarée, sans toucher aux fichiers."""
    if not isinstance(decision, dict) or decision.get("action") not in {
        "confirmer", "sauter", "retour", "reformuler", "abandonner"
    }:
        raise ErreurGuide("Décision inconnue pour ce plan.")
    if state["statut"] == "abandonne":
        raise ErreurGuide("Ce plan a été abandonné.")
    action = decision["action"]
    position = state["position"]
    etapes = deepcopy(state["etapes"])
    statuts = list(state["statuts"])
    observations = list(state.get("observations", ["", "", ""]))
    preuve = decision.get("observation", "")
    if not isinstance(preuve, str) or len(preuve) > 2000:
        raise ErreurGuide("L’observation doit être un texte de 2 000 caractères maximum.")
    if action == "abandonner":
        return {"statut": "abandonne"}
    if action == "retour":
        if position <= 0:
            raise ErreurGuide("Il n’y a pas d’étape précédente.")
        position -= 1
        statuts[position:] = ["a_faire"] * (len(etapes) - position)
        observations[position:] = [""] * (len(etapes) - position)
    elif position >= len(etapes):
        raise ErreurGuide("Reviens à une étape avant de modifier la progression.")
    elif action == "reformuler":
        champs = decision.get("champs")
        if not isinstance(champs, dict) or set(champs) != set(CHAMPS_TEXTE):
            raise ErreurGuide("Tous les champs de l’étape sont nécessaires à la reformulation.")
        etapes[position].update({
            champ: _texte(champs[champ], maxi) for champ, maxi in CHAMPS_TEXTE.items()
        })
        statuts[position:] = ["a_faire"] * (len(etapes) - position)
        observations[position:] = [""] * (len(etapes) - position)
    else:
        statuts[position] = "confirmee" if action == "confirmer" else "sautee"
        observations[position] = preuve.strip()
        position += 1
    return {
        "etapes": etapes, "position": position, "statuts": statuts, "observations": observations,
        "statut": "parcouru" if position == len(etapes) else "en_cours",
    }


def preparer_etapes_manuelles(recommandation, sources):
    """Encadrer la proposition sans inventer une correction ni un résultat de test.

    Les cas de test précis sont à compléter par le développeur avec « reformuler ».
    La suggestion issue du modèle reste une proposition à examiner.
    """
    refs = list(sources)
    etapes = [
        {
            "objectif": "Examiner la proposition et préparer un retour arrière.",
            "action": (
                "Ouvrir les passages cités et vérifier que le problème décrit est présent. "
                "Créer une copie distincte des fichiers concernés avant toute modification. "
                "Choisir un nom de copie qui n’existe pas déjà."
            ),
            "resultat_attendu": (
                "La proposition est comprise et une copie de l’état initial est disponible. "
                "Le code du projet n’a pas encore changé."
            ),
            "verification": (
                "Ouvrir la copie et comparer son contenu au fichier initial. "
                "Vérifier que la règle visée correspond aux passages cités ; sinon, abandonner "
                "le plan ou reformuler la proposition avant de commencer."
            ),
            "retour_arriere": (
                "À ce stade, aucune restauration du code n’est nécessaire : "
                "seule une copie distincte a été créée."
            ),
            "references": refs,
        },
        {
            "objectif": "Réaliser le changement choisi dans l’éditeur.",
            "action": (
                f"Dans les fichiers cités, appliquer manuellement ce changement : "
                f"{recommandation['proposition']} "
                "Limiter la modification à cette proposition, puis sauvegarder le fichier."
            ),
            "resultat_attendu": f"Bénéfice visé, à vérifier ensuite : {recommandation['benefice']}",
            "verification": (
                "Comparer le fichier modifié à la copie initiale. Repérer les lignes changées "
                "et contrôler leur cohérence avec les règles documentées. Cette relecture "
                "ne remplace pas les essais de l’étape suivante."
            ),
            "retour_arriere": (
                "Restaurer manuellement les seules lignes modifiées depuis la copie initiale. "
                "Conserver les autres modifications éventuelles du projet."
            ),
            "references": refs,
        },
        {
            "objectif": "Vérifier un cas normal et un cas limite lié au changement.",
            "action": (
                "Préparer une copie d’essai isolée. Choisir un cas normal et un cas qui révèle "
                "le problème initial. Avec « Reformuler cette étape », noter pour chacun "
                "les entrées, l’état initial et le résultat attendu AVANT l’essai. "
                "Déduire ces attentes des règles du code, puis réaliser les essais manuellement."
            ),
            "resultat_attendu": (
                "Les résultats observés correspondent aux attentes définies avant les essais. "
                "Le cas normal conserve son comportement et le cas limite respecte la règle visée. "
                "Les résultats restent à déterminer tant que les cas précis ne sont pas renseignés."
            ),
            "verification": (
                "Comparer pour les deux cas le résultat observé au résultat attendu. "
                "Vérifier aussi l’état après l’opération et, si une erreur est attendue, "
                "ses effets éventuels. En cas d’écart, revenir à l’étape précédente. "
                "Confirmer seulement après avoir renseigné puis vérifié les deux cas."
            ),
            "retour_arriere": (
                "Remettre la copie d’essai dans son état initial. Si la correction est rejetée, "
                "restaurer les seules lignes modifiées dans le projet depuis la copie de sauvegarde."
            ),
            "references": refs,
        },
    ]
    return valider_etapes({"etapes": etapes}, sources)


def build_guided_plan_graph(chunks: list[Chunk], recommandation,
                            max_context_chars: int = MAX_CONTEXT_CHARS):
    """Préparer trois étapes puis attendre une décision à chaque étape.

    Un InMemorySaver propre à ce graphe conserve le guide dans la session.
    Les étapes parcourues restent consultables via « retour » jusqu'à abandon.
    Aucune fonction de ce module ne lit, n'écrit ou n'exécute les fichiers du projet.
    """
    recommandation, contexte = _contexte_cible(tuple(chunks), recommandation, max_context_chars)

    def preparer(state: GuideState):
        etapes = preparer_etapes_manuelles(recommandation, contexte["sources"])
        return {
            "recommandation": recommandation, "context": contexte["context"],
            "sources": contexte["sources"], "etapes": etapes,
            "position": 0, "statuts": ["a_faire"] * 3, "statut": "en_cours", "observations": ["", "", ""],
        }

    def attendre(state: GuideState):
        # Rien d'effectué avant interrupt : ce nœud reprend depuis le début à la relance.
        decision = interrupt({"position": state["position"], "statut": state["statut"]})
        return {"decision": decision}

    def decider(state: GuideState):
        return appliquer_decision(state, state["decision"])

    builder = StateGraph(GuideState)
    builder.add_node("preparer_plan", preparer)
    builder.add_node("attendre_decision", attendre)
    builder.add_node("appliquer_decision", decider)
    builder.add_edge(START, "preparer_plan")
    builder.add_edge("preparer_plan", "attendre_decision")
    builder.add_edge("attendre_decision", "appliquer_decision")
    builder.add_conditional_edges("appliquer_decision", lambda state: (
        END if state["statut"] == "abandonne" else "attendre_decision"
    ))
    return builder.compile(checkpointer=InMemorySaver())


def reprendre_guide(graphe, config, decision):
    """Valider avant la reprise pour laisser le point d'attente intact en cas d'erreur."""
    snapshot = graphe.get_state(config)
    if not snapshot.values or not snapshot.next:
        raise ErreurGuide("Ce plan n’attend plus de décision.")
    appliquer_decision(snapshot.values, decision)
    return graphe.invoke(Command(resume=decision), config)
