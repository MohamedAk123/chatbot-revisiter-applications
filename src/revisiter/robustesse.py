"""Contrôles locaux des citations et messages d'erreur sans détail sensible."""

import re


class ErreurReponse(ValueError):
    pass


def reference_couverte(reference, sources):
    def plage(ref):
        m = re.fullmatch(r"(.+):L([1-9]\d*)(?:-L([1-9]\d*))?", ref)
        return (m[1], int(m[2]), int(m[3] or m[2])) if m else None
    cible = plage(reference)
    if not cible or cible[1] > cible[2]:
        return False
    prochaine = cible[1]
    for debut, fin in sorted((p[1], p[2]) for s in sources if (p := plage(s)) and p[0] == cible[0]):
        if fin < prochaine:
            continue
        if debut > prochaine:
            return False
        prochaine = fin + 1
        if prochaine > cible[2]:
            return True
    return False


def controler_citations(texte, sources):
    citations = re.findall(r"\[([^\]\n]+:L\d+(?:-L\d+)?)\]", texte)
    if any(not reference_couverte(ref, sources) for ref in citations):
        raise ErreurReponse("La réponse cite des lignes absentes du contexte. Elle n’a pas été enregistrée ; reformulez la question.")
    return "checked" if citations else "missing" if sources else "not_applicable"


def message_erreur(erreur):
    from httpx import ConnectError, TimeoutException
    from ollama import ResponseError
    if isinstance(erreur, ErreurReponse):
        return str(erreur)
    if isinstance(erreur, TimeoutException):
        return "Ollama n’a pas répondu à temps. Le modèle peut être en cours de chargement ; réessayez après ce chargement."
    if isinstance(erreur, (ConnectError, ConnectionError)):
        return "Ollama est inaccessible. Lancez Ollama puis vérifiez sa disponibilité avec ollama list."
    if isinstance(erreur, ResponseError):
        if erreur.status_code == 404:
            return "Le modèle configuré est introuvable. Vérifiez son nom avec ollama list."
        if erreur.status_code == 402:
            return "Les crédits du service cloud sont épuisés. Utilisez le mode local ou vérifiez votre compte."
        return "Ollama a refusé la requête. Vérifiez le modèle configuré et réessayez."
    if isinstance(erreur, (ImportError, OSError)):
        return "Une dépendance ou un fichier nécessaire est indisponible. Lancez le diagnostic de l’environnement."
    return "La réponse n’a pas pu être préparée. Vérifiez la configuration et le diagnostic ; la dernière analyse valide reste disponible."
