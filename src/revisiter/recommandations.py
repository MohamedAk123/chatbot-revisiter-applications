"""Propositions structurées, avec contrôle des références avant affichage."""

import re
from typing import Annotated, Literal, TypedDict

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.utils.function_calling import convert_to_json_schema
from langgraph.graph import END, START, StateGraph

from revisiter.ingestion import Chunk
from revisiter.workflow import (
    MAX_CONTEXT_CHARS,
    SYSTEM_PROMPT,
    SummaryState,
    prepare_project_context,
)


class Recommandation(TypedDict):
    """Une amélioration proposée et justifiée par les extraits."""

    titre: Annotated[str, ..., "Titre court et concret de la proposition."]
    justification: Annotated[str, ..., "Constat visible dans les extraits, en une phrase."]
    proposition: Annotated[str, ..., "Changement manuel proposé, en une phrase."]
    benefice: Annotated[str, ..., "Bénéfice attendu, sans gain chiffré inventé."]
    priorite: Literal["haute", "moyenne", "basse"]
    effort: Literal["faible", "moyen", "important"]
    references: Annotated[list[str], ..., "Références exactes fournies, sans crochets."]


class ReponseRecommandations(TypedDict):
    """De zéro à trois propositions, accompagnées des limites de l'analyse."""

    recommandations: list[Recommandation]
    limites: Annotated[str, ..., "Limites de la sélection et informations à confirmer."]


class RecommendationState(SummaryState, total=False):
    recommandations: list[Recommandation]
    limites: str


class ErreurRecommandations(ValueError):
    """Erreur contrôlée dont le message peut être affiché sans données privées."""


def _lire_plage(reference: str):
    correspondance = re.fullmatch(r"(.+):L([1-9]\d*)-L([1-9]\d*)", reference)
    if correspondance is None:
        return None
    source, debut, fin = correspondance.groups()
    debut, fin = int(debut), int(fin)
    return (source, debut, fin) if debut <= fin else None


def _reference_couverte(reference: str, sources: list[str]) -> bool:
    """Accepter une plage précise si toutes ses lignes ont été fournies."""
    demandee = _lire_plage(reference)
    if demandee is None:
        return False
    fichier, debut, fin = demandee
    intervalles = []
    for source in sources:
        plage = _lire_plage(source)
        if plage is not None and plage[0] == fichier:
            intervalles.append((plage[1], plage[2]))

    prochaine_ligne = debut
    for debut_fourni, fin_fournie in sorted(intervalles):
        if fin_fournie < prochaine_ligne:
            continue
        if debut_fourni > prochaine_ligne:
            return False
        prochaine_ligne = fin_fournie + 1
        if prochaine_ligne > fin:
            return True
    return False


def valider_recommandations(resultat, sources: list[str]) -> ReponseRecommandations:
    """Vérifier le format et l'appartenance des références aux extraits envoyés.

    Ce contrôle ne juge pas la justesse de la proposition : la lecture du code
    et la décision du développeur restent nécessaires.
    """
    if not isinstance(resultat, dict) or set(resultat) != {"recommandations", "limites"}:
        raise ErreurRecommandations("La réponse ne respecte pas le format demandé.")
    propositions = resultat["recommandations"]
    if not isinstance(propositions, list) or len(propositions) > 3:
        raise ErreurRecommandations("Une liste de zéro à trois recommandations est attendue.")

    def texte_court(valeur, maximum):
        if not isinstance(valeur, str) or not valeur.strip() or len(valeur.strip()) > maximum:
            raise ErreurRecommandations("Un champ de recommandation est vide, invalide ou trop long.")
        return valeur.strip()

    limites = texte_court(resultat["limites"], 800)
    champs_texte = {"titre": 160, "justification": 800, "proposition": 800, "benefice": 500}
    champs = set(champs_texte) | {"priorite", "effort", "references"}
    validees = []
    for proposition in propositions:
        if not isinstance(proposition, dict) or set(proposition) != champs:
            raise ErreurRecommandations("Les champs d'une recommandation sont incomplets ou inattendus.")
        copie = {champ: texte_court(proposition[champ], maxi)
                 for champ, maxi in champs_texte.items()}
        if proposition["priorite"] not in ("haute", "moyenne", "basse"):
            raise ErreurRecommandations("Priorité de recommandation invalide.")
        if proposition["effort"] not in ("faible", "moyen", "important"):
            raise ErreurRecommandations("Estimation d'effort invalide.")
        refs = proposition["references"]
        if not isinstance(refs, list) or not 1 <= len(refs) <= 5:
            raise ErreurRecommandations("Chaque recommandation doit citer entre un et cinq extraits.")
        references = []
        for reference in refs:
            reference = texte_court(reference, 600)
            if reference.startswith("[") and reference.endswith("]"):
                reference = reference[1:-1].strip()
            if not _reference_couverte(reference, sources):
                raise ErreurRecommandations(
                    "Une référence cite un fichier ou des lignes absents des extraits fournis."
                )
            if reference not in references:
                references.append(reference)
        copie.update(
            priorite=proposition["priorite"], effort=proposition["effort"], references=references
        )
        validees.append(copie)

    ordre = {"haute": 0, "moyenne": 1, "basse": 2}
    validees.sort(key=lambda proposition: ordre[proposition["priorite"]])
    return {"recommandations": validees, "limites": limites}


def build_recommendation_graph(
    chunks: list[Chunk], model, max_context_chars: int = MAX_CONTEXT_CHARS
):
    """Préparer les extraits, proposer des améliorations, valider le résultat."""
    if not chunks or max_context_chars < 1:
        raise ValueError("Des extraits et une taille de contexte positive sont requis.")
    chunks = tuple(chunks)

    def prepare(state: RecommendationState):
        return prepare_project_context(chunks, max_context_chars)

    def propose(state: RecommendationState):
        schema = convert_to_json_schema(ReponseRecommandations)
        # Le périmètre est connu du programme et ne dépend pas d'un jugement du LLM.
        schema["properties"].pop("limites")
        schema["required"].remove("limites")
        schema["description"] = "De zéro à trois améliorations proposées à partir des extraits."
        schema["additionalProperties"] = False
        liste = schema["properties"]["recommandations"]
        liste["maxItems"] = 3
        liste["items"]["additionalProperties"] = False
        references = liste["items"]["properties"]["references"]
        references.update(minItems=1, maxItems=5)
        references["items"] = {"type": "string", "enum": sorted(set(state["sources"]))}
        modele_structure = model.with_structured_output(schema, method="json_schema")
        messages = [
            SystemMessage(content=(
                SYSTEM_PROMPT
                + "\nPropose de zéro à trois améliorations concrètes du code présenté. "
                "Utilise le format structuré demandé, en français et avec des phrases courtes. "
                "Compare d'abord le calcul aux règles explicites des commentaires et docstrings. "
                "Cherche les cas limites où des entrées autorisées pourraient produire un résultat "
                "contraire à une règle documentée. Distingue les conditions sur les entrées "
                "des garanties sur le résultat ou sur l'état après une opération. "
                "Si un tel cas existe, décris ce cas concret par lecture du code et propose "
                "en priorité de protéger cette garantie. Ne prétends pas avoir exécuté ce cas. "
                "Classe haute une correction qui prévient une violation d'une règle explicite ; "
                "moyenne une amélioration de robustesse supplémentaire ; basse une amélioration "
                "de lisibilité ou de documentation. "
                "Pour chaque proposition, précise le constat, le changement proposé, "
                "le bénéfice, la priorité, l'effort estimé et les références exactes. "
                "Donne un titre qui décrit l'action précise. Évite les conseils vagues comme "
                "ajouter de la gestion d'erreurs : indique les conditions à contrôler et "
                "le comportement attendu. Regroupe les propositions qui traitent le même problème. "
                "Recopie uniquement les références des extraits fournis. "
                "Respecte les comportements explicitement documentés dans le code. "
                "Prends en compte la documentation déjà présente dans l'en-tête du fichier "
                "avant de recommander une documentation supplémentaire. "
                "L'absence de tests ou de contrôles dans ces extraits ne prouve pas "
                "leur absence dans le projet. Signale les vérifications nécessaires. "
                "Si les éléments ne justifient pas trois propositions, propose-en moins. "
                "N'affirme jamais avoir épuisé les améliorations possibles du projet. "
                "La priorité et l'effort sont des estimations à confirmer."
            )),
            HumanMessage(content=(
                f"Nom du projet : {state.get('project_name', '').strip() or 'non renseigné'}\n"
                f"Extraits sélectionnés : {state['selected_chunks']} / {state['total_chunks']}\n"
                f"Fichiers représentés : {len(state['covered_files'])} / {state['total_files']}\n\n"
                f"EXTRAITS FOURNIS\n{state['context']}"
            )),
        ]
        try:
            resultat = modele_structure.invoke(messages)
        except OutputParserException as erreur:
            raise ErreurRecommandations(
                "La réponse d’Ollama ne respecte pas le format demandé. Relance l’analyse."
            ) from erreur
        if not isinstance(resultat, dict):
            raise ErreurRecommandations("La réponse ne respecte pas le format demandé.")
        limites = (
            f"Analyse fondée sur {state['selected_chunks']}/{state['total_chunks']} extraits "
            f"et {len(state['covered_files'])}/{state['total_files']} fichiers importés. "
            "Ces propositions constituent une sélection à examiner et peuvent être complétées. "
            "Priorités et efforts sont estimés."
        )
        return valider_recommandations({**resultat, "limites": limites}, state["sources"])

    builder = StateGraph(RecommendationState)
    builder.add_node("preparer_ameliorations", prepare)
    builder.add_node("proposer_ameliorations", propose)
    builder.add_edge(START, "preparer_ameliorations")
    builder.add_edge("preparer_ameliorations", "proposer_ameliorations")
    builder.add_edge("proposer_ameliorations", END)
    return builder.compile()
