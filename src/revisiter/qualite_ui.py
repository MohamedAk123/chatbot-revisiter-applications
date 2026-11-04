"""Contrôles de preuves, secrets, versions, validation et mesures dans les vues existantes."""

import hashlib
import json

import httpx
import streamlit as st

from revisiter.benchmark_projet import comparer_rapports, evaluer_jeu, modele_jeu, valider_jeu
from revisiter.config import load_settings
from revisiter.diagnostic_requetes import resume_traces
from revisiter.ollama_client import create_chat_model
from revisiter.projets import lire_github, lire_zip
from revisiter.validation_isolee import diagnostic_isolation, executer_validation
from revisiter.versions import comparer_versions


def afficher_preuves(assessment, resolution=None):
    if isinstance(resolution, dict) and resolution.get('subject'):
        st.caption('Sujet repris : ' + resolution['subject'])
    if not isinstance(assessment, dict) or not isinstance(assessment.get('checks'), list):
        return
    with st.expander('Vérification des affirmations et hypothèses'):
        st.caption(assessment['scope'])
        if assessment['checks']:
            st.dataframe([{'Élément contrôlé': c['claim'], 'Contrôle': c['kind'], 'Résultat': c['status']}
                          for c in assessment['checks']], hide_index=True)
        else:
            st.info('Aucune affirmation ne correspond aux règles de contrôle déterministes. La réponse reste à relire.')
        if assessment['hypotheses']:
            st.caption('Formulations hypothétiques repérées :')
            for hypothese in assessment['hypotheses']:
                st.text(hypothese)
        st.caption(assessment['limits'])


def preferences_securite():
    with st.sidebar.expander('Secrets et instructions dans les fichiers'):
        masquer = st.checkbox('Masquer les secrets avant analyse et sauvegarde', value=True, key='masquer_secrets')
        exclure = st.checkbox('Écarter les fichiers contenant des instructions suspectes', value=True, key='exclure_instructions')
        st.caption('Détection par motifs : des secrets peuvent échapper au contrôle, et un exemple documentaire peut être signalé.')
        zone = st.empty()
    return masquer, exclure, zone


def afficher_securite(zone, constats, exclus, masque):
    if not constats:
        return
    with zone.container():
        st.warning(f'{len(constats)} signalement(s) ; {len(exclus)} fichier(s) écarté(s).')
        st.dataframe(constats[:200], hide_index=True)
        if not masque and any(c['kind'] != 'instruction suspecte à examiner' for c in constats):
            st.warning('Masquage désactivé : les valeurs détectées peuvent être transmises au modèle et enregistrées dans cet espace.')
        st.caption('Les signalements affichent uniquement les emplacements et catégories, jamais les valeurs détectées.')


def afficher_diagnostic_requetes():
    with st.expander('Diagnostic des requêtes de cette session'):
        traces = st.session_state.get('traces_requetes', [])
        st.caption('Mesures locales, sans questions ni code. Les sous-durées peuvent se recouvrir ; elles ne doivent pas être additionnées.')
        if not traces:
            st.info('Posez une question pour obtenir les premières mesures.')
            return
        resume = resume_traces(traces)
        st.write(f"{resume['requests']} requêtes · {resume['errors']} erreurs · {resume['cancelled']} annulations")
        if resume['p50_s'] is not None:
            st.caption(f"Réponses terminées : médiane {resume['p50_s']:.2f} s · 95e percentile {resume['p95_s']:.2f} s")
        st.dataframe(traces, hide_index=True)
        st.download_button('Télécharger les mesures sans contenu', data=json.dumps({'summary': resume, 'traces': traces}, indent=2),
                           file_name='diagnostic_requetes.json', key='export_diagnostic_requetes', on_click='ignore')


def afficher_validation_isolee(documents, proposition, tests):
    with st.expander('Valider la proposition dans un environnement isolé'):
        st.caption('Python/pytest et JavaScript/node:test. La validation utilise une copie jetable : aucun montage du projet, réseau coupé, ressources et durée limitées.')
        diagnostic = diagnostic_isolation() if st.button('Vérifier le moteur isolé', key='diagnostic_isolation') else None
        if diagnostic:
            (st.success if diagnostic['ready'] else st.warning)(diagnostic['reason'])
        st.caption('Préparation de l’image, une fois Docker local installé : python -m revisiter.validation_isolee --preparer')
        if st.button('Exécuter ces tests sur la proposition isolée', key='executer_validation_isolee', type='primary'):
            try:
                with st.spinner('Validation dans le conteneur local…'):
                    st.session_state.validation_isolee = executer_validation(documents, proposition, tests)
            except (ValueError, TypeError, KeyError, OSError) as erreur:
                st.error(str(erreur))
        rapport = st.session_state.get('validation_isolee')
        signature = hashlib.sha256((proposition['code'] + '\0' + tests['code']).encode()).hexdigest()
        if rapport and rapport.get('validation_sha256') == signature:
            st.write('Résultat : ' + rapport['status'])
            if rapport.get('reason'):
                st.info(rapport['reason'])
            if rapport.get('output'):
                st.code(rapport['output'], language='text')
            st.download_button('Télécharger le rapport de validation isolée', data=json.dumps(rapport, ensure_ascii=False, indent=2),
                               file_name='validation_isolee.json', key='export_validation_isolee', on_click='ignore')


def afficher_comparaison_versions(documents):
    with st.expander('Comparer avec une version précédente'):
        st.caption('Le projet importé est la version actuelle. Choisissez un instantané de référence ; les secrets des diffs sont masqués.')
        mode = st.radio('Référence à comparer', ['Archive ZIP', 'GitHub public'], key='mode_version_reference', horizontal=True)
        archive = None
        if mode == 'Archive ZIP':
            archive = st.file_uploader('Archive de la version précédente', type=['zip'], key='archive_version_reference')
        else:
            url = st.text_input('Dépôt GitHub public de référence', key='url_version_reference')
            revision = st.text_input('Commit, branche ou tag de référence', value='HEAD', key='revision_reference')
        if st.button('Comparer les deux versions', key='comparer_versions', disabled=not documents):
            try:
                if mode == 'Archive ZIP':
                    if not callable(getattr(archive, 'getvalue', None)):
                        raise ValueError('Ajoutez une archive ZIP de référence.')
                    importation = lire_zip(archive.getvalue())
                else:
                    importation = lire_github(url, revision)
                avant = {f.name: f.getvalue().decode('utf-8-sig') for f in importation.fichiers}
                if not avant:
                    raise ValueError('Aucun fichier exploitable dans la référence sélectionnée.')
                st.session_state.comparaison_versions = comparer_versions(avant, documents)
            except (ValueError, TypeError, OSError, httpx.HTTPError) as erreur:
                st.error(str(erreur))
        resultat = st.session_state.get('comparaison_versions')
        if resultat:
            if resultat['files']:
                st.dataframe(resultat['files'], hide_index=True)
                fichier = st.selectbox('Diff à consulter', list(resultat['diffs']), key='fichier_diff_version')
                st.code(resultat['diffs'][fichier] or 'Changement masqué : relire les valeurs sensibles localement.', language='diff')
            else:
                st.info('Aucune différence textuelle dans les fichiers comparés.')
            if resultat['possible_moves']:
                st.dataframe(resultat['possible_moves'], hide_index=True)
            st.caption(f"{len(resultat['impacts'])} fonction(s) candidate(s) à examiner avant et après le changement.")
            if resultat['impacts']:
                choix = st.selectbox('Impact à examiner', range(len(resultat['impacts'])),
                                     format_func=lambda i: resultat['impacts'][i]['version'] + ' · ' + resultat['impacts'][i]['symbol'] + ' · ' + resultat['impacts'][i]['reference'],
                                     key='impact_diff_version')
                st.dataframe([{'Appelant': n['name'], 'Fichier': n['file'], 'Niveau': n['distance'],
                               'Résolution': n['confidence'], 'Référence': n['call_reference']}
                              for n in resultat['impacts'][choix]['impact']['callers']], hide_index=True)
            if resultat.get('limited'):
                st.warning('Analyse d’impact partielle : réduisez le périmètre de comparaison.')
            st.caption(resultat['limits'])
            st.download_button('Télécharger la comparaison et ses impacts', data=json.dumps(resultat, ensure_ascii=False, indent=2),
                               file_name='comparaison_versions.json', key='export_versions', on_click='ignore')


def afficher_benchmark_projet(documents):
    with st.expander('Tests de qualité sur votre propre projet'):
        st.caption('Sélectionnez du code réel, définissez les réponses et références attendues puis comparez les rapports. Les critères automatiques restent mécaniques ; les raisonnements demandent une revue humaine.')
        fichiers = st.multiselect('Fichiers du jeu de qualité', sorted(documents), default=sorted(documents)[:3], key='benchmark_fichiers')
        if fichiers:
            try:
                modele = modele_jeu({n: documents[n] for n in fichiers})
                st.download_button('Télécharger le modèle de jeu à compléter', data=json.dumps(modele, ensure_ascii=False, indent=2),
                                   file_name='jeu_qualite_projet.json', key='export_modele_benchmark', on_click='ignore')
                st.caption('Ce téléchargement contient le code sélectionné. Complétez cases, contains, sources et rubric dans le fichier JSON.')
            except ValueError as erreur:
                st.info(str(erreur))
        fichier = st.file_uploader('Jeu de qualité JSON', type=['json'], key='import_benchmark')
        inclure = st.checkbox('Inclure les cas interprétatifs avec Ollama local', key='benchmark_llm')
        if st.button('Évaluer le jeu du projet', key='executer_benchmark', disabled=bool(st.session_state.get('_execution_active'))):
            try:
                if not callable(getattr(fichier, 'getvalue', None)):
                    raise TypeError('Ajoutez un jeu de qualité JSON.')
                jeu = valider_jeu(fichier.getvalue())
                modele, nom = None, 'sans modèle'
                if inclure:
                    configuration = load_settings()
                    if configuration.mode != 'local':
                        raise ValueError('Ce banc d’essai est limité à Ollama local.')
                    modele, nom = create_chat_model(configuration), configuration.model
                progression = st.progress(0)
                st.session_state.benchmark_projet = evaluer_jeu(jeu, modele, nom, progression.progress)
            except (ValueError, TypeError, KeyError, OSError) as erreur:
                st.error(str(erreur))
        resultat = st.session_state.get('benchmark_projet')
        if not resultat:
            return
        st.caption(f"{resultat['checks_passed']} critères mécaniques réussis sur {resultat['executed']} cas exécutés.")
        jeton_revue = hashlib.sha256((resultat['dataset_sha256'] + resultat.get('created_utc', '')).encode()).hexdigest()[:16]
        for c in resultat['cases']:
            with st.expander(c['id'] + ' · ' + c['status']):
                st.text(c.get('answer', 'Cas non exécuté.'))
                st.caption(c.get('rubric', ''))
                if 'answer' in c:
                    avis = ['à relire', 'acceptable', 'incorrect']
                    precedent = resultat['reviews'].get(c['id'], 'à relire')
                    resultat['reviews'][c['id']] = st.selectbox('Revue humaine', avis,
                        index=avis.index(precedent) if precedent in avis else 0,
                        key='revue_benchmark_' + jeton_revue + c['id'])
                else:
                    st.caption('Aucune réponse produite : pas de validation humaine à attribuer.')
        resultat['human_review_pending'] = sum(v == 'à relire' for v in resultat['reviews'].values())
        precedent = st.file_uploader('Rapport précédent à comparer', type=['json'], key='rapport_benchmark_precedent')
        if callable(getattr(precedent, 'getvalue', None)):
            try:
                if precedent.size > 5_000_000:
                    raise ValueError('Rapport trop volumineux.')
                comparaison = comparer_rapports(json.loads(precedent.getvalue()), resultat)
                st.dataframe(comparaison, hide_index=True)
            except (ValueError, TypeError, KeyError) as erreur:
                st.error(str(erreur))
        st.download_button('Télécharger le rapport du projet et les revues', data=json.dumps(resultat, ensure_ascii=False, indent=2),
                           file_name='qualite_projet.json', key='export_benchmark_projet', on_click='ignore')
