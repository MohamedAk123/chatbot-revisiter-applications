"""Lecture statique multilangage : aucune importation ni exécution du code analysé."""

import ast
import json
from pathlib import Path

LANGAGES = {
    ".py": "python", ".pyi": "python", ".js": "javascript", ".mjs": "javascript",
    ".cjs": "javascript", ".jsx": "javascript", ".ts": "typescript", ".tsx": "tsx",
    ".java": "java", ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp",
    ".hpp": "cpp", ".cs": "csharp", ".go": "go", ".rs": "rust", ".php": "php",
    ".rb": "ruby", ".kt": "kotlin", ".swift": "swift", ".json": "json",
}
FONCTIONS = {"function_definition", "function_declaration", "method_declaration",
             "method_definition", "function_item", "method", "constructor_declaration"}
CLASSES = {"class_definition", "class_declaration", "class_specifier", "struct_item",
           "struct_specifier", "interface_declaration", "trait_item", "enum_declaration"}


def _signature(noeud, donnees):
    feuilles, pile = [], [noeud]
    while pile:
        courant = pile.pop()
        if "comment" in courant.type:
            continue
        if courant.children:
            pile.extend(reversed(courant.children))
        else:
            feuilles.append(donnees[courant.start_byte:courant.end_byte].decode("utf-8"))
    return json.dumps(feuilles, ensure_ascii=False)


def analyser_structure(nom, contenu):
    langage = LANGAGES.get(Path(nom).suffix.lower())
    resultat = {"language": langage, "status": "unsupported", "symbols": [], "calls": []}
    if not langage:
        return resultat
    lignes = contenu.splitlines()
    try:
        if langage == "python":
            racine = ast.parse(contenu)
            pile = [(racine, "")]
            while pile:
                noeud, prefixe = pile.pop()
                if isinstance(noeud, ast.Call) and isinstance(noeud.func, (ast.Name, ast.Attribute)):
                    cible = noeud.func.id if isinstance(noeud.func, ast.Name) else noeud.func.attr
                    debut, fin = noeud.func.lineno, noeud.func.end_lineno
                    resultat["calls"].append({"name": cible, "line": debut,
                        "reference": f"{nom}:L{debut}-L{fin}", "header": "\n".join(lignes[debut - 1:fin])})
                if isinstance(noeud, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    nom_symbole = prefixe + noeud.name
                    ligne = noeud.lineno
                    resultat["symbols"].append({
                        "name": nom_symbole, "kind": "class" if isinstance(noeud, ast.ClassDef) else "function",
                        "line": ligne, "end_line": noeud.end_lineno,
                        "reference": f"{nom}:L{ligne}-L{ligne}", "header": lignes[ligne - 1],
                        "signature": ast.dump(noeud.args, include_attributes=False)
                        if isinstance(noeud, (ast.FunctionDef, ast.AsyncFunctionDef)) else "class",
                    })
                    prefixe = nom_symbole + "."
                pile.extend((enfant, prefixe) for enfant in reversed(list(ast.iter_child_nodes(noeud))))
        else:
            try:
                from tree_sitter_language_pack import get_parser
            except ImportError:
                return {**resultat, "status": "unavailable"}
            parseur = get_parser(langage)
            donnees = contenu.encode("utf-8")
            arbre = parseur.parse(donnees)
            if arbre.root_node.has_error:
                return {**resultat, "status": "error"}
            pile = [arbre.root_node]
            visites = 0
            while pile:
                noeud = pile.pop()
                visites += 1
                if visites > 100_000:
                    return {**resultat, "status": "limit", "symbols": []}
                genre = "function" if noeud.type in FONCTIONS else "class" if noeud.type in CLASSES else None
                if noeud.type in {"call_expression", "method_invocation", "invocation_expression", "function_call_expression", "call"}:
                    fonction = noeud.child_by_field_name("function") or noeud.child_by_field_name("name") or noeud.child_by_field_name("method")
                    if fonction:
                        cible = donnees[fonction.start_byte:fonction.end_byte].decode("utf-8")
                        debut, fin = fonction.start_point.row + 1, fonction.end_point.row + 1
                        resultat["calls"].append({"name": cible, "line": debut,
                            "reference": f"{nom}:L{debut}-L{fin}", "header": "\n".join(lignes[debut - 1:fin])})
                if noeud.type == "variable_declarator":
                    valeur = noeud.child_by_field_name("value")
                    if valeur and valeur.type in {"arrow_function", "function_expression"}:
                        genre = "function"
                nom_noeud = noeud.child_by_field_name("name")
                if genre and nom_noeud is None:
                    declarateur = noeud.child_by_field_name("declarator")
                    if declarateur:
                        nom_noeud = declarateur.child_by_field_name("declarator")
                if genre and nom_noeud:
                    nom_symbole = donnees[nom_noeud.start_byte:nom_noeud.end_byte].decode("utf-8")
                    ligne = nom_noeud.start_point.row + 1
                    params = noeud.child_by_field_name("parameters")
                    if params is None and noeud.type == "variable_declarator":
                        valeur = noeud.child_by_field_name("value")
                        if valeur:
                            params = valeur.child_by_field_name("parameters")
                    if params is None:
                        declarateur = noeud.child_by_field_name("declarator")
                        if declarateur:
                            params = declarateur.child_by_field_name("parameters")
                    signature = _signature(params, donnees) if params else genre
                    resultat["symbols"].append({"name": nom_symbole, "kind": genre, "line": ligne,
                        "end_line": noeud.end_point.row + 1, "reference": f"{nom}:L{ligne}-L{ligne}",
                        "header": lignes[ligne - 1], "signature": signature})
                pile.extend(reversed(noeud.named_children))
    except (ImportError, OSError):
        return {**resultat, "status": "unavailable", "symbols": [], "calls": []}
    except (SyntaxError, RecursionError, ValueError, LookupError, RuntimeError):
        return {**resultat, "status": "error", "symbols": []}
    resultat["status"] = "ok"
    return resultat


def inventaire(documents, genre="function", maximum=80, budget=6000, analyses=None):
    symboles, nombre_analyses, exclus = [], 0, []
    for nom, contenu in sorted(documents.items()):
        analyse = analyses[nom] if analyses and nom in analyses else analyser_structure(nom, contenu)
        if analyse["status"] != "ok":
            exclus.append(nom)
            continue
        nombre_analyses += 1
        symboles.extend(s for s in analyse["symbols"] if s["kind"] == genre)
    affiches, taille = [], 0
    for symbole in symboles:
        cout = len(symbole["reference"]) + len(symbole["header"]) + 6
        if taille + cout <= budget and len(affiches) < maximum:
            affiches.append(symbole)
            taille += cout
    texte = f"Déclarations nommées repérées par analyse statique : {nombre_analyses}/{len(documents)} fichiers analysés.\n\n"
    texte += "\n".join(f"- `{s['name']}` [{s['reference']}]" for s in affiches)
    if not symboles:
        texte += "Aucune déclaration de ce type repérée dans les fichiers analysés."
    if len(affiches) < len(symboles):
        texte += f"\n\nAffichage de {len(affiches)}/{len(symboles)} déclarations."
    if exclus:
        texte += "\n\nFichiers non analysés (format, parseur ou syntaxe) : " + ", ".join(exclus)
    contexte = "\n\n".join(f"[{s['reference']}]\n{s['header']}" for s in affiches)
    return texte, [s["reference"] for s in affiches], contexte


def appels_nommes(documents, cible, budget=6000, analyses=None):
    import re
    occurrences, exclus, taille = [], [], 0
    for nom, code in sorted(documents.items()):
        analyse = analyses[nom] if analyses and nom in analyses else analyser_structure(nom, code)
        if analyse["status"] != "ok":
            exclus.append(nom)
            continue
        for appel in analyse["calls"]:
            cout = len(appel["reference"]) + len(appel["header"]) + 6
            if re.search(r"(?<!\w)" + re.escape(cible) + r"(?!\w)", appel["name"]) and taille + cout <= budget:
                occurrences.append(appel)
                taille += cout
    texte = f"Appels syntaxiques repérés pour `{cible}` :\n\n"
    texte += "\n".join(f"- [{a['reference']}] : `{a['header'].strip()}`" for a in occurrences)
    if not occurrences:
        texte += "Aucun appel de ce nom repéré dans les fichiers analysés."
    texte += "\n\nRecherche par nom d’appel ; les alias, liaisons dynamiques et chemins d’exécution restent à vérifier."
    if exclus:
        texte += "\nFichiers non analysés : " + ", ".join(exclus)
    return texte, [a["reference"] for a in occurrences], "\n\n".join(f"[{a['reference']}]\n{a['header']}" for a in occurrences)
