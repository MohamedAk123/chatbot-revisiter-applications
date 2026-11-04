"""Décrire les éléments visibles des fichiers sans inférer une intention métier."""

import ast
import re
from pathlib import Path

from revisiter.lecteurs import _extraire_sections
from revisiter.structures import analyser_structure


def est_demande_roles(question):
    """Réserver le raccourci aux descriptions de fichiers, sans demande composée."""
    if not re.search(r"\bfichiers?\b", question):
        return False
    if re.search(
        r"\b(pourquoi|comment|risqu\w*|problemes?|erreurs?|amelior\w*|corrig\w*|"
        r"modifi\w*|chang\w*|transform\w*|tests?|securit\w*|metier|architecture|"
        r"performances?|fonctions?|methodes?|classes?)\b",
        question,
    ):
        return False
    return bool(re.search(r"\b(roles?|utilite|responsabilites?|sert|servent)\b", question))


def _code(texte):
    """Un fragment littéral, y compris s'il contient des accents graves."""
    marque = "`" * (max((len(m) for m in re.findall(r"`+", texte)), default=0) + 1)
    return f"{marque} {texte} {marque}"


def _faits_fichier(nom, contenu, analyse):
    lignes = contenu.splitlines()

    def fait(texte, debut, fin):
        extrait = "\n".join(lignes[debut - 1:fin])
        if extrait.strip() and len(extrait) <= 1600:
            return texte, f"{nom}:L{debut}-L{fin}", extrait
        return None

    faits = []
    suffixe = Path(nom).suffix.lower()
    if suffixe in {".md", ".mdx", ".txt"}:
        # Le texte du document reste une citation, même s'il contient des consignes.
        visibles = [(i, l.strip()) for i, l in enumerate(lignes, 1) if l.strip()][:2]
        for i, ligne in visibles:
            faits.append(fait("Texte de documentation : " + _code(ligne[:180]) + ".", i, i))
    elif analyse["status"] == "ok":
        langage = analyse.get("language") or suffixe.lstrip(".")
        for s in sorted(analyse["symbols"], key=lambda s: s["line"])[:4]:
            genre = "classe" if s["kind"] == "class" else "fonction ou méthode"
            faits.append(fait(f"Code {langage} : définit la {genre} {_code(s['name'])}.",
                              s["line"], s["line"]))
        if suffixe in {".py", ".pyi"}:
            # Une expression retournée est décrite littéralement, jamais exécutée.
            try:
                racine = ast.parse(contenu)
                for noeud in racine.body:
                    if isinstance(noeud, (ast.Import, ast.ImportFrom)):
                        texte = ast.get_source_segment(contenu, noeud)
                        if texte and len(texte) <= 180:
                            faits.append(fait("Import visible : " + _code(texte) + ".",
                                              noeud.lineno, noeud.end_lineno))
                    elif isinstance(noeud, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        corps = noeud.body
                        if (corps and isinstance(corps[0], ast.Expr)
                                and isinstance(corps[0].value, ast.Constant)
                                and isinstance(corps[0].value.value, str)):
                            corps = corps[1:]
                        if len(corps) == 1 and isinstance(corps[0], ast.Return):
                            retour = corps[0]
                            expression = ast.get_source_segment(contenu, retour.value) if retour.value else 'None'
                            if expression and len(expression) <= 180:
                                faits.append(fait(f"{_code(noeud.name)} retourne directement {_code(expression)}.",
                                                  retour.lineno, retour.end_lineno))
            except (SyntaxError, RecursionError, ValueError):
                pass
        else:
            for appel in sorted(analyse["calls"], key=lambda a: a["line"])[:2]:
                faits.append(fait("Appel présent : " + _code(appel["name"]) + ".",
                                  appel["line"], appel["line"]))
    if not any(faits):
        statut = {
            "error": "Lecture syntaxique incomplète",
            "unavailable": "Parseur indisponible",
            "limit": "Limite de lecture syntaxique atteinte",
        }.get(analyse["status"], "Contenu textuel visible")
        for i, ligne in enumerate(lignes, 1):
            if ligne.strip() and len(ligne) <= 1600:
                faits.append(fait(statut + " : " + _code(ligne.strip()[:180]) + ".", i, i))
                break
    return [f for f in faits if f]


def roles_fichiers(documents, question, budget=6000, analyses=None):
    """Retourner texte, références et preuves bornées ; signaler toute omission."""
    cibles = {n: c for n, c in documents.items() if n.casefold() in question.casefold()}
    if not cibles:
        cibles = {n: c for n, c in documents.items() if re.search(
            r"(?<![\w./])" + re.escape(Path(n).name) + r"(?![\w./])", question, re.IGNORECASE)}
    selection = cibles or documents
    paragraphes, sources, preuves, representes = [], [], {}, []
    for nom, contenu in sorted(selection.items()):
        if nom.lower().endswith(".ipynb"):
            faits = []
            for source, genre, texte in _extraire_sections(nom, contenu)[:4]:
                lignes = texte.splitlines()
                if lignes and len(lignes[0]) <= 1600:
                    faits.append((f"Notebook Jupyter, cellule {genre} : {_code(lignes[0][:180])}.",
                                  f"{source}:L1-L1", lignes[0]))
        else:
            analyse = analyses[nom] if analyses and nom in analyses else analyser_structure(nom, contenu)
            # Une question ciblant une fonction dans un fichier reste interprétative.
            question_symboles = question
            for cible in cibles:
                question_symboles = re.sub(re.escape(cible), "", question_symboles, flags=re.IGNORECASE)
                question_symboles = re.sub(re.escape(Path(cible).name), "", question_symboles, flags=re.IGNORECASE)
            if any(re.search(r"(?<!\w)" + re.escape(s["name"]) + r"(?!\w)", question_symboles)
                   for s in analyse["symbols"]):
                return None
            faits = _faits_fichier(nom, contenu, analyse)
        entete = f"**{_code(nom)}**"
        phrases = []
        for texte, ref, extrait in faits:
            preuve = f"[{ref}]\n{extrait}"
            nouvelle_taille = len("\n\n".join([*preuves.values(), *([] if ref in preuves else [preuve])]))
            phrase = f"{texte} [{ref}]"
            cout = len("\n\n".join([*paragraphes, entete + '\n' + ' '.join([*phrases, phrase])]))
            if nouvelle_taille > budget or cout > budget - 300:
                continue
            phrases.append(phrase)
            preuves[ref] = preuve
            if ref not in sources:
                sources.append(ref)
        if phrases:
            representes.append(nom)
            paragraphes.append(entete + "\n" + " ".join(phrases))
        if len(representes) >= 40:
            break
    if not paragraphes:
        return None
    texte = f"Rôle visible des fichiers — {len(representes)}/{len(selection)} fichiers décrits :\n\n"
    texte += "\n\n".join(paragraphes)
    texte += "\n\nDescription fondée sur les déclarations, expressions et textes visibles."
    if len(representes) < len(selection):
        texte += " Affichage limité par le budget ; précisez un fichier pour poursuivre."
    return texte, sources, "\n\n".join(preuves.values())
