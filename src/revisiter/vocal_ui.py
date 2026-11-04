"""Microphone natif, transcription locale et validation de la question reconnue."""

import streamlit as st

from revisiter.vocal import (
    MAX_QUESTION_CHARS,
    ErreurVocale,
    transcription_disponible,
    transcrire_audio,
)

LANGUES_UI = {'Français': 'fr', 'Détection automatique': None, 'English': 'en', 'العربية': 'ar'}
CLES_BROUILLON = ('vocal_en_attente', 'vocal_texte', 'vocal_note')


def effacer_brouillon():
    for cle in CLES_BROUILLON:
        st.session_state.pop(cle, None)


def saisie_question(contexte, *, disabled=False):
    # Un brouillon vocal ne doit pas suivre un changement de compte ou de projet.
    if st.session_state.get('_vocal_contexte') != contexte:
        effacer_brouillon()
        st.session_state._vocal_contexte = contexte
    disponible = transcription_disponible()
    with st.expander('Parler au chatbot', icon=':material/mic:'):
        langue = st.selectbox('Langue de la question vocale', list(LANGUES_UI), key='vocal_langue')
        if disponible:
            st.caption('Cliquez sur le micro dans la saisie, parlez, arrêtez puis envoyez l’enregistrement. '
                       'Relisez la transcription avant de l’envoyer au chatbot. Maximum : 60 secondes.')
            st.caption('Transcription sur cet ordinateur. L’audio n’est pas ajouté aux espaces enregistrés. '
                       'Le premier chargement du modèle peut prendre plus de temps.')
        else:
            st.info('Le texte est disponible. Pour afficher le microphone, installez les dépendances vocales puis redémarrez l’application.')
    valeur = st.chat_input('Posez une question sur votre projet…', max_chars=MAX_QUESTION_CHARS,
                          accept_audio=disponible, audio_sample_rate=16_000, max_upload_size=4,
                          submit_mode='disable', disabled=disabled)
    texte, audio = '', None
    if isinstance(valeur, str):
        texte = valeur.strip()
    elif valeur is not None:
        texte = (valeur.text or '').strip()
        audio = valeur.audio
    if audio is not None:
        # Remplacer aussi l'ancien brouillon en cas d'échec du nouvel enregistrement.
        effacer_brouillon()
        try:
            with st.spinner('Transcription locale de votre question…'):
                reconnu = transcrire_audio(audio.getvalue(), LANGUES_UI[langue])
            brouillon = '\n'.join(p for p in (texte, reconnu) if p)
            if len(brouillon) > MAX_QUESTION_CHARS:
                raise ErreurVocale('Texte et transcription dépassent 1 500 caractères. Envoyez une question plus courte.')
            st.session_state.vocal_texte = brouillon
            st.session_state.vocal_en_attente = True
        except ErreurVocale as erreur:
            st.error(str(erreur))
        texte = ''  # Aucun appel au chatbot avant validation du texte reconnu.
    elif texte:
        effacer_brouillon()
        return texte

    zone = st.empty()
    if st.session_state.get('vocal_en_attente'):
        with zone.container(border=True):
            st.markdown('**Relire la question reconnue**')
            st.caption('Corrigez si nécessaire les noms de fonctions et de fichiers avant l’envoi.')
            with st.form('confirmation_vocale'):
                question = st.text_area('Question transcrite', key='vocal_texte',
                                        max_chars=MAX_QUESTION_CHARS, height=110)
                envoyer, annuler = st.columns(2)
                confirme = envoyer.form_submit_button('Envoyer la question', type='primary', icon=':material/send:', disabled=disabled)
                abandonne = annuler.form_submit_button('Annuler', icon=':material/close:')
            if abandonne:
                effacer_brouillon()
                zone.empty()
            elif confirme:
                if not question.strip():
                    st.warning('La question est vide. Saisissez votre question ou annulez.')
                else:
                    resultat = question.strip()
                    effacer_brouillon()
                    zone.empty()
                    return resultat
    return None
