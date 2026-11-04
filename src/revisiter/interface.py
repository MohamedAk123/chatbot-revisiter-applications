"""Présentation de l'espace Revisiter, indépendante des modèles et du contenu du code."""

import re
from base64 import b64encode
from html import escape
from pathlib import Path

import streamlit as st

from revisiter.securite_analyse import bloquer_images_externes

ICONS = {
    "code": '<path d="m8 7-5 5 5 5m8-10 5 5-5 5m-3-13-2 16"/>',
    "file": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
            '<path d="M14 2v6h6M8 13h8M8 17h5"/>',
    "layers": '<path d="m12 3 10 5-10 5L2 8zM2 12l10 5 10-5M2 16l10 5 10-5"/>',
    "spark": '<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z"/>',
    "check": '<rect x="3" y="3" width="18" height="18" rx="5"/><path d="m8 12 3 3 5-6"/>',
    "chat": '<path d="M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l-3 3V11.5A8.5 8.5 0 0 1 9.5 3h3a8.5 '
            '8.5 0 0 1 8.5 8.5Z"/><path d="M7 10h8M7 14h5"/>',
    "download": '<path d="M12 3v12m-5-5 5 5 5-5M4 16v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4"/>',
    "shield": '<path d="m12 3 8 3v5c0 5-4 8-8 10-4-2-8-5-8-10V6z"/><path d="m8 12 3 3 5-6"/>',
    "diagram": '<rect x="8" y="2" width="8" height="5" rx="1"/><path d="M12 7v5M5 12h14M5 12v4m14-4v4"/>'
               '<rect x="2" y="16" width="6" height="5" rx="1"/><rect x="16" y="16" width="6" height="5" rx="1"/>',
}


def icone(nom, couleur="#0f766e"):
    svg = (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" '
        f'aria-hidden="true">{ICONS[nom]}</svg>'
    ).replace('stroke="currentColor"', f'stroke="{couleur}"')
    # st.html utilise un profil HTML : les icônes passent par une image statique,
    # plutôt que par un élément SVG supprimé par son assainissement du contenu.
    svg = svg.replace('<svg ', '<svg xmlns="http://www.w3.org/2000/svg" ', 1)
    data = b64encode(svg.encode("utf-8")).decode("ascii")
    return f'<img class="rv-glyph" src="data:image/svg+xml;base64,{data}" alt="" aria-hidden="true">'


def appliquer_style():
    css = Path(__file__).with_name("interface.css").read_text(encoding="utf-8")
    st.html(f"<style>{css}</style>")


def marque():
    st.html(
        f'<div class="rv-brand"><span class="rv-brand-mark">{icone("code", "#ffffff")}</span>'
        '<div><strong>revisiter<span>.</span></strong>'
        '<small>Votre atelier de code</small></div></div>'
    )


def introduction(nom_projet, nombre_fichiers):
    projet = escape(nom_projet.strip() or "Votre atelier de code")
    quantite = "1 fichier ajouté" if nombre_fichiers == 1 else f"{nombre_fichiers} fichiers ajoutés"
    detail = (
        "Comprenez le code, explorez ses liens et préparez vos changements."
        if nombre_fichiers else "Importez un projet pour comprendre son code et préparer vos changements."
    )
    st.html(
        '<header class="rv-page-header"><div>'
        '<div class="rv-topline">Espace projet</div>'
        f'<h1>{projet}</h1><p>{escape(detail)}</p></div>'
        f'<span class="rv-project-name">{icone("file")}{quantite if nombre_fichiers else "Aucun fichier ajouté"}</span>'
        '</header>'
    )


def indicateurs(nombre_fichiers, nombre_passages, recommandations, guide):
    confirmees = guide["statuts"].count("confirmee") if guide else 0
    total = len(guide["etapes"]) if guide else 0
    cartes = [
        ("file", str(nombre_fichiers), "Fichiers importés"),
        ("layers", str(nombre_passages), "Passages préparés"),
        ("spark", str(len(recommandations)), "Pistes d’amélioration"),
        ("check", f"{confirmees} / {total}" if total else "—", "Étapes confirmées"),
    ]
    html = '<div class="rv-stats">'
    for symbole, valeur, libelle in cartes:
        html += (
            f'<article class="rv-stat">'
            f'<span class="rv-icon">{icone(symbole)}</span>'
            f'<div><strong>{valeur}</strong><div class="rv-stat-label">{libelle}</div>'
            '</div></article>'
        )
    st.html(html + "</div>")


def accueil_discussion(avec_fichiers):
    description = (
        "Choisissez une piste ci-dessous, ou posez directement votre question."
        if avec_fichiers else "Importez vos fichiers pour commencer. Vous pouvez aussi poser une question générale."
    )
    st.html(
        f'<div class="rv-chat-welcome"><span class="rv-welcome-icon">{icone("spark")}</span>'
        '<span class="rv-eyebrow">COMPRENDRE · EXPLORER · TRANSFORMER</span>'
        '<h2>Un nouveau regard sur votre projet</h2>'
        f'<p>{description}</p></div>'
    )


def entete_action(symbole, numero):
    st.html(
        '<div class="rv-action-top" aria-hidden="true">'
        f'<span class="rv-action-icon">{icone(symbole)}</span>'
        '<span class="rv-action-number">↗</span></div>'
    )


def sources_message(references):
    if not references:
        return
    groupes = {}
    uniques = list(dict.fromkeys(references))
    for reference in uniques:
        fichier = reference.rsplit(':L', 1)[0]
        groupes.setdefault(fichier, []).append(reference)
    lignes = []
    for refs in groupes.values():
        etiquettes = ''.join(
            f'<span class="rv-source-range" title="{escape(r, quote=True)}" '
            f'aria-label="{escape(r, quote=True)}">'
            f'{escape(r if i == 0 else "L" + r.rsplit(":L", 1)[-1])}</span>'
            for i, r in enumerate(refs)
        )
        lignes.append(f'<div class="rv-source-file">{icone("file")}{etiquettes}</div>')
    compte = f'{len(groupes)} fichier(s) · {len(uniques)} passage(s)'
    st.html('<details class="rv-sources" aria-label="Références des passages">'
            '<summary><span class="rv-sources-label">Sources</span>'
            f'<span class="rv-sources-count">{compte}</span></summary>'
            '<div class="rv-source-list">' + ''.join(lignes) + '</div></details>')


def formater_reponse(texte):
    """Séparer les titres de fichiers de l'inventaire sans toucher au code généré."""
    texte = bloquer_images_externes(texte)
    if texte.startswith('Rôle visible des fichiers — '):
        return re.sub(r'(?m)^(\*\*`+[^\n]+?`+\*\*)\n(?=\S)', r'\1\n\n', texte)
    return texte


def etat_vide(symbole, titre, description):
    st.html(
        f'<div class="rv-empty"><span class="rv-empty-icon">{icone(symbole)}</span>'
        f'<h3>{escape(titre)}</h3><p>{escape(description)}</p></div>'
    )


def progression_guide(etat):
    libelles = {"a_faire": "À faire", "confirmee": "Confirmée", "sautee": "Sautée"}
    html = '<div class="rv-stepper">'
    for i, (titre, statut) in enumerate(zip(("Préparer", "Modifier", "Vérifier"), etat["statuts"], strict=True)):
        actif = " current" if etat["statut"] == "en_cours" and etat["position"] == i else ""
        signe = "✓" if statut == "confirmee" else str(i + 1)
        html += (
            f'<div class="rv-step {statut}{actif}"><b>{signe}</b><div>'
            f'<strong>{titre}</strong><small>{libelles[statut]}</small></div></div>'
        )
    st.html(html + "</div>")


def priorite(priorite, effort):
    couleurs = {"haute": "high", "moyenne": "medium", "basse": "low"}
    st.html(
        f'<div class="rv-tags"><span class="rv-priority {couleurs[priorite]}">'
        f'Priorité estimée : {escape(priorite)}</span><span class="rv-effort">'
        f'Effort estimé : {escape(effort)}</span></div>'
    )
