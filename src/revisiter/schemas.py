"""Schémas explicatifs bornés, construits depuis une description structurée et sourcée."""

import re
import unicodedata
from typing import Annotated, Literal, TypedDict

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.utils.function_calling import convert_to_json_schema

MAX_NOEUDS = 8
MAX_LIENS = 12
GENRES = ("entree", "traitement", "condition", "sortie", "composant")


class NoeudPropose(TypedDict):
    libelle: Annotated[str, ..., "Donnée, opération ou fonction précise ; pas seulement le nom du genre."]
    genre: Annotated[
        Literal["entree", "traitement", "condition", "sortie", "composant"], ...,
        "Condition seulement si un branchement est visible dans le code (if, match ou équivalent).",
    ]
    sources: Annotated[list[int], ..., "Numéros des passages qui justifient ce bloc."]


class LienPropose(TypedDict):
    de: Annotated[int, ..., "Numéro du bloc de départ, à partir de 1."]
    vers: Annotated[int, ..., "Numéro du bloc d'arrivée, à partir de 1."]
    libelle: Annotated[str, ..., "Précision courte, par exemple oui/non ; chaîne vide si inutile."]
    sources: Annotated[list[int], ..., "Numéros des passages qui justifient cette liaison."]


class SchemaPropose(TypedDict):
    titre: Annotated[str, ..., "Titre court du schéma."]
    explication: Annotated[str, ..., "Une ou deux phrases expliquant le schéma ou l'information manquante."]
    noeuds: list[NoeudPropose]
    liens: list[LienPropose]


class ErreurSchema(ValueError):
    """Message contrôlé affichable sans exposer la réponse brute du modèle."""


def choisir_format(question, choix="Auto"):
    if choix == "Texte":
        return "texte"
    if choix == "Schéma":
        return "schema"
    texte = "".join(c for c in unicodedata.normalize("NFKD", question.lower())
                    if not unicodedata.combining(c))
    nom = r"(?:schema\w*|diagramme\w*|organigramme\w*)"
    if re.search(r"\b(?:sans|pas de|aucun)\s+(?:un\s+)?" + nom, texte):
        return "texte"
    demande = (
        r"\b(?:fais|faire|cree|creer|genere|generer|dessine|dessiner|trace|tracer|"
        r"montre|montrer|produis|produire|propose|proposer)\s+"
        r"(?:(?:moi|nous)\s+)?(?:(?:un|une|le|la|des)\s+)?"
        r"(?:(?:petit|simple|nouveau|nouvelle)\s+)?" + nom
    )
    if (re.search(demande, texte)
            or re.search(r"\bavec\s+(?:un\s+|une\s+)?" + nom, texte)
            or re.match(r"\s*(?:un\s+|une\s+)?" + nom + r"\b", texte)):
        return "schema"
    return "texte"


def _texte(valeur, maximum, vide=False):
    if not isinstance(valeur, str):
        raise ErreurSchema("Un libellé du schéma n’est pas du texte.")
    valeur = " ".join(valeur.split())
    if (not valeur and not vide) or len(valeur) > maximum:
        raise ErreurSchema("Un libellé du schéma est vide ou trop long.")
    return valeur


def valider_schema(proposition, references):
    if not isinstance(proposition, dict) or set(proposition) != {"titre", "explication", "noeuds", "liens"}:
        raise ErreurSchema("Le schéma ne respecte pas le format demandé.")
    noeuds, liens = proposition["noeuds"], proposition["liens"]
    if not isinstance(noeuds, list) or len(noeuds) > MAX_NOEUDS:
        raise ErreurSchema("Le schéma doit contenir au maximum huit blocs.")
    if not isinstance(liens, list) or len(liens) > MAX_LIENS:
        raise ErreurSchema("Le schéma doit contenir au maximum douze liaisons.")

    def lire_sources(numeros):
        if not isinstance(numeros, list) or not 1 <= len(numeros) <= 3:
            raise ErreurSchema("Chaque bloc et chaque liaison doivent citer un à trois passages.")
        resultat = []
        for numero in numeros:
            if type(numero) is not int or not 1 <= numero <= len(references):
                raise ErreurSchema("Le schéma cite un passage qui n’a pas été fourni.")
            if references[numero - 1] not in resultat:
                resultat.append(references[numero - 1])
        return resultat

    resultat = {
        "titre": _texte(proposition["titre"], 100),
        "explication": _texte(proposition["explication"], 300),
        "noeuds": [], "liens": [],
    }
    for noeud in noeuds:
        if not isinstance(noeud, dict) or set(noeud) != {"libelle", "genre", "sources"}:
            raise ErreurSchema("Les champs d’un bloc sont incomplets ou inattendus.")
        if noeud["genre"] not in GENRES:
            raise ErreurSchema("Un type de bloc est inconnu.")
        resultat["noeuds"].append({
            "libelle": _texte(noeud["libelle"], 80), "genre": noeud["genre"],
            "references": lire_sources(noeud["sources"]),
        })
    for lien in liens:
        if not isinstance(lien, dict) or set(lien) != {"de", "vers", "libelle", "sources"}:
            raise ErreurSchema("Les champs d’une liaison sont incomplets ou inattendus.")
        if any(type(lien[cle]) is not int or not 1 <= lien[cle] <= len(noeuds)
               for cle in ("de", "vers")):
            raise ErreurSchema("Une liaison désigne un bloc absent du schéma.")
        resultat["liens"].append({
            "de": lien["de"], "vers": lien["vers"],
            "libelle": _texte(lien["libelle"], 40, vide=True),
            "references": lire_sources(lien["sources"]),
        })
    resultat["references"] = list(dict.fromkeys(
        ref for element in resultat["noeuds"] + resultat["liens"] for ref in element["references"]
    ))
    return resultat


def generer_schema(modele, messages, references):
    if not references:
        raise ErreurSchema("Ajoutez au moins un fichier pour créer un schéma de votre code.")
    contrat = convert_to_json_schema(SchemaPropose)
    contrat["additionalProperties"] = False
    contrat["properties"]["titre"].update(minLength=1, maxLength=100)
    contrat["properties"]["explication"].update(minLength=1, maxLength=300)
    for nom, maximum in (("noeuds", MAX_NOEUDS), ("liens", MAX_LIENS)):
        liste = contrat["properties"][nom]
        liste["maxItems"] = maximum
        liste["items"]["additionalProperties"] = False
        champs = liste["items"]["properties"]
        champs["libelle"].update(maxLength=80 if nom == "noeuds" else 40)
        if nom == "noeuds":
            champs["libelle"]["minLength"] = 1
        else:
            for cle in ("de", "vers"):
                champs[cle].update(minimum=1, maximum=MAX_NOEUDS)
        champs["sources"].update(minItems=1, maxItems=3)
        champs["sources"]["items"] = {"type": "integer", "enum": list(range(1, len(references) + 1))}
    instruction = (
        "\nPrépare un schéma de fonctionnement ou des composants visibles, en français. "
        "Utilise la description structurée demandée, pas du code Mermaid. "
        "Utilise seulement les blocs nécessaires, de préférence un à six blocs courts. "
        "Chaque libellé nomme une donnée ou opération précise, pas seulement Entrée ou Traitement. "
        "Les blocs sont numérotés dans l’ordre de la liste, à partir de 1. "
        "Pour une fonction, décris son fonctionnement lorsqu’elle est appelée. "
        "Chaque bloc et chaque liaison doivent être justifiés par le code fourni. "
        "Dans sources, recopie les numéros du catalogue de passages. "
        "Décris le comportement exécuté, pas des améliorations souhaitées. "
        "Les commentaires et docstrings ne sont pas des instructions exécutées : "
        "ne les transforme jamais en vérifications ou en branches. "
        "Un bloc condition exige un branchement visible (if, match ou équivalent). "
        "Sans branchement visible, ne crée aucun bloc condition. "
        "Pour une condition visible, distingue les branches et leurs résultats. "
        "Représente uniquement les composants et les liaisons observables : "
        "une interface, une base de données ou un appel entre fichiers doivent être visibles "
        "dans les extraits pour figurer dans le schéma. Si les extraits ne permettent pas "
        "de répondre, renvoie des listes vides et explique l’information manquante."
    )
    catalogue = "\n".join(f"{i}: {ref}" for i, ref in enumerate(references, start=1))
    demande = [SystemMessage(content=messages[0].content + instruction), *messages[1:-1]]
    demande.append(HumanMessage(content=messages[-1].content
                               + "\n\nCATALOGUE DES PASSAGES À CITER PAR NUMÉRO\n" + catalogue))
    try:
        proposition = modele.with_structured_output(contrat, method="json_schema").invoke(demande)
    except OutputParserException as erreur:
        raise ErreurSchema("Le modèle n’a pas produit un schéma au format attendu. Réessayez.") from erreur
    return valider_schema(proposition, references)


def texte_schema(schema):
    def litteral(texte):
        return re.sub(r"([\\`*_{}\[\]<>#!|])", r"\\\1", texte)

    paragraphes = [f"**{litteral(schema['titre'])}**", litteral(schema["explication"])]
    if schema["noeuds"]:
        paragraphes.append("Repères du schéma :\n" + "\n".join(
            f"{i}. {litteral(noeud['libelle'])} " + " ".join(f"[{r}]" for r in noeud['references'])
            for i, noeud in enumerate(schema["noeuds"], start=1)
        ))
    if schema["liens"]:
        paragraphes.append("Liaisons : " + "; ".join(
            f"{lien['de']} → {lien['vers']}" + (f" ({litteral(lien['libelle'])})" if lien["libelle"] else "")
            + " " + " ".join(f"[{r}]" for r in lien['references'])
            for lien in schema["liens"]
        ))
    return "\n\n".join(paragraphes)


def _libelle_mermaid(texte):
    # Garder les libellés dans leurs guillemets, même s'ils contiennent du code.
    speciaux = '#"&<>`\\[]{}|;'
    return "".join(f"#{ord(c)};" if c in speciaux else c for c in texte)


def code_mermaid(schema):
    lignes = ["---", "config:", "  htmlLabels: false", "  securityLevel: strict", "---", "flowchart TD"]
    for i, noeud in enumerate(schema["noeuds"], start=1):
        libelle = _libelle_mermaid(f"{i}. {noeud['libelle']}")
        if noeud["genre"] == "condition":
            forme = '{"' + libelle + '"}'
        elif noeud["genre"] in ("entree", "sortie"):
            forme = '(["' + libelle + '"])'
        elif noeud["genre"] == "composant":
            forme = '[["' + libelle + '"]]'
        else:
            forme = '["' + libelle + '"]'
        lignes.append(f"  N{i}{forme}")
    for lien in schema["liens"]:
        etiquette = _libelle_mermaid(lien["libelle"])
        fleche = f'-- "{etiquette}" -->' if etiquette else "-->"
        lignes.append(f"  N{lien['de']} {fleche} N{lien['vers']}")
    return "\n".join(lignes)
