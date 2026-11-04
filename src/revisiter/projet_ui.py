"""Sélecteur d’import de projet, avec état local à la session."""

import hashlib

import httpx
import streamlit as st

from revisiter.ingestion import ALLOWED_EXTENSIONS
from revisiter.projets import ImportProjet, fichiers_importes, lire_github, lire_zip


def selection_import():
    mode = st.radio('Importer depuis', ['Fichiers', 'Dossier', 'Archive ZIP', 'GitHub public'], key='source_import', horizontal=True)
    try:
        if mode in {'Fichiers', 'Dossier'}:
            fichiers = st.file_uploader('Sélectionnez les fichiers du projet', type=sorted(ALLOWED_EXTENSIONS),
                accept_multiple_files='directory' if mode == 'Dossier' else True, key='fichiers_projet')
            resultat = fichiers_importes(fichiers or [], origine=mode)
        elif mode == 'Archive ZIP':
            archive = st.file_uploader('Archive du projet', type=['zip'], key='archive_projet', max_upload_size=30)
            if archive is None:
                return ImportProjet()
            donnees = archive.getvalue()
            cle = hashlib.sha256(donnees).hexdigest()
            if st.session_state.get('cle_archive') != cle:
                st.session_state.archive_importee = lire_zip(donnees)
                st.session_state.cle_archive = cle
            resultat = st.session_state.archive_importee
        else:
            with st.form('import_github'):
                url = st.text_input('URL du dépôt GitHub public', placeholder='https://github.com/organisation/projet')
                revision = st.text_input('Branche, tag ou commit', value='HEAD')
                charger = st.form_submit_button('Charger un instantané du dépôt')
            st.caption('Lecture seule. Pour un dépôt privé, utilisez Dossier ou Archive ZIP.')
            if charger:
                with st.spinner('Téléchargement de l’instantané…'):
                    nouveau = lire_github(url, revision)
                st.session_state.git_importe = nouveau
            resultat = st.session_state.get('git_importe', ImportProjet())
        if resultat.ignores:
            with st.expander(f'{len(resultat.ignores)} fichier(s) ignoré(s)'):
                st.caption('Les dossiers de dépendances, fichiers cachés, formats non pris en charge et fichiers trop volumineux sont écartés.')
                for nom, raison in resultat.ignores[:100]:
                    st.text(nom + ' : ' + raison)
        if resultat.fichiers:
            st.caption(f'{resultat.origine} · {len(resultat.fichiers)} fichier(s) retenu(s)')
        return resultat
    except (ValueError, httpx.HTTPError, OSError) as erreur:
        st.error(str(erreur) if isinstance(erreur, ValueError) else 'Impossible de lire cet instantané. Vérifiez la source et réessayez.')
        st.stop()
