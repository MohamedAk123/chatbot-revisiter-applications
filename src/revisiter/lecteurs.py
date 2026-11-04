"""Préparer l'aperçu et les extraits sans exécuter les fichiers importés."""

import json

from revisiter.ingestion import Chunk


def numeroter(texte: str) -> str:
    return "\n".join(
        f"L{numero}: {ligne}"
        for numero, ligne in enumerate(texte.splitlines(), start=1)
    )


def _extraire_sections(nom: str, contenu: str) -> list[tuple[str, str, str]]:
    if not nom.lower().endswith(".ipynb"):
        return [(nom, "texte", contenu)]

    try:
        notebook = json.loads(contenu)
    except json.JSONDecodeError as erreur:
        raise ValueError("Le notebook contient un JSON invalide.") from erreur

    if not isinstance(notebook, dict) or notebook.get("nbformat") != 4:
        raise ValueError("Un notebook Jupyter au format 4 est attendu.")

    cellules = notebook.get("cells")
    if not isinstance(cellules, list):
        raise ValueError(  # noqa: TRY004 - Validation du contenu JSON.
            "La liste des cellules du notebook est invalide."
        )

    sections = []
    for index, cellule in enumerate(cellules, start=1):
        if not isinstance(cellule, dict):
            raise ValueError(  # noqa: TRY004 - Validation du contenu JSON.
                f"La cellule {index} est invalide."
            )

        type_cellule = cellule.get("cell_type")
        if type_cellule not in ("code", "markdown"):
            continue

        source = cellule.get("source", "")
        if isinstance(source, list) and all(isinstance(s, str) for s in source):
            source = "".join(source)
        if not isinstance(source, str):
            raise ValueError(  # noqa: TRY004 - Validation du contenu JSON.
                f"Le contenu de la cellule {index} est invalide."
            )

        if source.strip():
            sections.append((f"{nom}#cellule{index}", type_cellule, source))

    if not sections:
        raise ValueError(
            "Le notebook ne contient aucune cellule de code ou Markdown non vide."
        )
    return sections


def preparer_document(nom: str, contenu: str) -> str:
    """Conserver l'aperçu numéroté utilisé par l'interface."""
    blocs = []
    for source, type_section, texte in _extraire_sections(nom, contenu):
        entete = f"Fichier : {source}\n"
        if type_section != "texte":
            entete += f"Type : {type_section}\n"
        blocs.append(entete + numeroter(texte))
    return "\n\n".join(blocs)


def preparer_extraits(
    nom: str, contenu: str, taille_max: int = 1_200
) -> list[Chunk]:
    """Découper sur une fin de ligne quand c'est possible.

    Une ligne plus longue que taille_max est répartie entre plusieurs extraits
    qui gardent son numéro. Chaque cellule du notebook reste distincte.
    """
    if taille_max < 1:
        raise ValueError("La taille des extraits doit être positive.")

    extraits = []
    for source, _, texte in _extraire_sections(nom, contenu):
        texte = "\n".join(texte.splitlines())
        position = 0
        ligne_debut = 1

        while position < len(texte):
            fin = min(position + taille_max, len(texte))
            if fin < len(texte):
                coupure = texte.rfind("\n", position, fin)
                if coupure >= position:
                    fin = coupure + 1

            bloc = texte[position:fin]
            passage = bloc.rstrip("\n")
            if passage.strip():
                extraits.append(
                    Chunk(
                        source=source,
                        start_line=ligne_debut,
                        end_line=ligne_debut + passage.count("\n"),
                        text=passage,
                    )
                )

            ligne_debut += bloc.count("\n")
            position = fin

    return extraits
