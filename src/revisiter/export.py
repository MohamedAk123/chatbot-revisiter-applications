"""Compte rendu Markdown de la session courante, sans nouvel appel au modèle."""

import re
from datetime import UTC, datetime

from revisiter.guide import PHASES
from revisiter.schemas import code_mermaid


def _bloc(texte):
    """Conserver le texte exact, même s'il contient déjà des blocs de code."""
    maximum = max((len(m.group()) for m in re.finditer(r"`+", texte)), default=0)
    cloture = "`" * max(3, maximum + 1)
    return f"{cloture}text\n{texte}\n{cloture}"


def _references(sources):
    if not sources:
        return "Références : aucune."
    return "Références :\n\n" + _bloc("\n".join(sources))


def _couverture(analyse):
    texte = (
        f"Couverture : {analyse['selected_chunks']} / {analyse['total_chunks']} extraits ; "
        f"{len(analyse['covered_files'])} / {analyse['total_files']} fichiers représentés."
    )
    if analyse["omitted_files"]:
        texte += "\n\nFichiers non représentés :\n\n" + _bloc("\n".join(analyse["omitted_files"]))
    return texte


def _proposition(proposition):
    return [
        _bloc(proposition["titre"]),
        f"Priorité estimée : {proposition['priorite']} ; effort estimé : {proposition['effort']}.",
        "**Justification :**\n\n" + _bloc(proposition["justification"]),
        "**Changement proposé :**\n\n" + _bloc(proposition["proposition"]),
        "**Bénéfice attendu :**\n\n" + _bloc(proposition["benefice"]),
        _references(proposition["references"]),
    ]


def generer_compte_rendu(
    nom_projet, fichiers, messages, *, vue_ensemble=None, ameliorations=None, guide=None, transformation=None, tests_regression=None
):
    """Exporter uniquement les champs lisibles de l'activité en mémoire.

    Les objets du modèle, les paramètres de connexion et les contextes bruts ne
    sont pas sérialisés. Le texte d'une réponse peut lui-même contenir du code.
    L'export n'est pas un fichier de sauvegarde permettant de reprendre la session.
    """
    blocs = [
        "# Compte rendu — Revisiter une application",
        "Projet :\n\n" + _bloc(nom_projet.strip() or "non renseigné"),
        "Exporté le " + datetime.now(UTC).strftime("%Y-%m-%d à %H:%M:%S UTC"),
        ("Ce document décrit l’état actuel de la session. Les analyses sont des propositions "
         "à examiner ; les statuts du guide sont déclarés par l’utilisateur."),
        "## Fichiers importés",
        _bloc("\n".join(sorted(set(fichiers)))) if fichiers else "Aucun fichier importé.",
        "## Vue d’ensemble",
    ]
    if vue_ensemble is None:
        blocs.append("Aucune vue d’ensemble préparée.")
    else:
        blocs.extend([
            _couverture(vue_ensemble), _bloc(vue_ensemble["answer"]),
            _references(vue_ensemble["sources"]),
        ])

    blocs.append("## Améliorations proposées")
    if ameliorations is None:
        blocs.append("Aucune analyse d’améliorations préparée.")
    else:
        blocs.append(_couverture(ameliorations))
        for numero, proposition in enumerate(ameliorations["recommandations"], start=1):
            blocs.append(f"### Proposition {numero}")
            blocs.extend(_proposition(proposition))
        if not ameliorations["recommandations"]:
            blocs.append("Aucune amélioration étayée proposée à partir des extraits.")
        blocs.append("**Limites de l’analyse :**\n\n" + _bloc(ameliorations["limites"]))

    blocs.append("## Plan d’amélioration guidé")
    if guide is None:
        blocs.append("Aucun plan préparé.")
    else:
        libelles = {
            "en_cours": "en cours", "parcouru": "parcours terminé", "abandonne": "abandonné",
            "a_faire": "à faire", "confirmee": "confirmée", "sautee": "sautée",
        }
        blocs.extend([
            (f"État du parcours : {libelles[guide['statut']]}. "
             f"Étapes confirmées : {guide['statuts'].count('confirmee')} ; "
             f"étapes sautées : {guide['statuts'].count('sautee')}."),
            "Les confirmations ne constituent pas une vérification automatique des fichiers.",
            "**Proposition choisie pour ce plan :**",
        ])
        blocs.extend(_proposition(guide["recommandation"]))
        for i, etape in enumerate(guide["etapes"]):
            blocs.append(f"### Étape {i + 1} — {PHASES[i]} — {libelles[guide['statuts'][i]]}")
            blocs.append("**Fichiers concernés :**\n\n" + _bloc("\n".join(etape["fichiers"])))
            for champ, libelle in (
                ("objectif", "Objectif"), ("action", "Action manuelle"),
                ("resultat_attendu", "Résultat attendu"), ("verification", "Vérification"),
                ("retour_arriere", "Retour arrière"),
            ):
                blocs.append(f"**{libelle} :**\n\n" + _bloc(etape[champ]))
            blocs.append(_references(etape["references"]))
            if guide.get("observations", ["", "", ""])[i]:
                blocs.append("**Observations déclarées :**\n\n" + _bloc(guide["observations"][i]))

    if transformation:
        blocs.extend(["## Proposition de code à relire", _bloc(transformation["title"]),
                      _bloc(transformation["summary"]), _bloc(transformation["diff"]),
                      "Syntaxe : " + transformation["syntax"] + ". Aucun test exécuté.",
                      _references(transformation["references"]),
                      "Cas proposés, à valider :\n\n" + _bloc("\n".join(transformation["tests"]))])
        for champ in ("objectif", "contraintes", "criteres"):
            if transformation.get("contract", {}).get(champ):
                blocs.append(champ.capitalize() + " :\n\n" + _bloc(transformation["contract"][champ]))
        blocs.append("Empreinte SHA-256 du fichier de départ : " + transformation["base_sha256"])

    if tests_regression and transformation:
        from revisiter.transformations import empreinte
        if tests_regression['target'] == transformation['file'] and tests_regression['target_sha256'] == empreinte(transformation['code']):
            blocs.extend(['## Tests de non-régression proposés', _bloc(tests_regression['file']),
                'Non exécutés. Imports, critères et assertions à relire.', _bloc(tests_regression['criteria']),
                _bloc(tests_regression['code'])])

    blocs.append("## Discussion")
    if not messages:
        blocs.append("Aucun message dans la discussion actuelle.")
    for numero, message in enumerate(messages, start=1):
        role = {"user": "Utilisateur", "assistant": "Assistant"}[message["role"]]
        blocs.extend([
            f"### Message {numero} — {role}", _bloc(message["content"]),
        ])
        if message["role"] == "assistant":
            blocs.append(_references(message.get("sources", [])))
            schema = message.get("schema")
            if schema and schema["noeuds"]:
                blocs.append("**Schéma explicatif :**\n\n```mermaid\n" + code_mermaid(schema) + "\n```")
                blocs.append(_references(schema["references"]))
    return "\n\n".join(blocs) + "\n"
