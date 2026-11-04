"""Reconnaissance locale de questions courtes, sans conserver l'enregistrement."""

import argparse
import importlib.util
import os
import sys
import wave
from array import array
from functools import lru_cache
from io import BytesIO
from threading import Lock

MAX_AUDIO_BYTES = 4 * 1024 * 1024
MAX_AUDIO_SECONDS = 60
MAX_QUESTION_CHARS = 1_500
LANGUES = {'fr', 'en', 'ar', None}
MODELES = {'tiny', 'base', 'small'}
_VERROU = Lock()


class ErreurVocale(ValueError):
    """Erreur présentable sans détails techniques ni contenu de l'audio."""


def transcription_disponible():
    return importlib.util.find_spec('faster_whisper') is not None


def nom_modele():
    nom = os.getenv('REVISITER_WHISPER_MODEL', 'base').strip()
    if nom not in MODELES:
        raise ErreurVocale('Le modèle vocal doit être tiny, base ou small.')
    return nom


def verifier_audio(donnees):
    """Vérifier le WAV PCM avant chargement du modèle et décodage audio."""
    if not isinstance(donnees, bytes) or not donnees:
        raise ErreurVocale('Enregistrez une question avec le microphone.')
    if len(donnees) > MAX_AUDIO_BYTES:
        raise ErreurVocale('Enregistrement trop volumineux : maximum 4 Mo et 60 secondes.')
    try:
        with wave.open(BytesIO(donnees), 'rb') as audio:
            frequence = audio.getframerate()
            canaux, largeur, trames = audio.getnchannels(), audio.getsampwidth(), audio.getnframes()
            if (audio.getcomptype() != 'NONE' or largeur != 2 or canaux not in (1, 2)
                    or not 8_000 <= frequence <= 48_000):
                raise ErreurVocale('Format audio non pris en charge. Réenregistrez avec le microphone du chat.')
            if trames <= 0:
                raise ErreurVocale('L’enregistrement est vide. Réessayez avec une courte question.')
            if trames > MAX_AUDIO_SECONDS * frequence:
                raise ErreurVocale('La question vocale dépasse 60 secondes. Enregistrez une question plus courte.')
            pcm = audio.readframes(trames)
            if len(pcm) != trames * canaux * largeur:
                raise ErreurVocale('L’enregistrement est incomplet. Réenregistrez votre question.')
    except (wave.Error, EOFError, OSError, OverflowError) as erreur:
        raise ErreurVocale('Audio illisible. Utilisez le microphone du chat pour enregistrer un nouveau message.') from erreur
    valeurs = array('h')
    valeurs.frombytes(pcm)
    if sys.byteorder != 'little':
        valeurs.byteswap()
    if not any(valeurs):
        raise ErreurVocale('Aucune parole audible. Vérifiez votre microphone et réessayez.')
    return trames / frequence


@lru_cache(maxsize=1)
def _charger_modele(nom):
    # L'appelant détient le verrou : pas de double chargement concurrent.
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise ErreurVocale('La transcription vocale n’est pas installée. Installez les dépendances vocales, puis relancez le chatbot.') from None
    options = {'device': 'cpu', 'compute_type': 'int8',
               'cpu_threads': min(os.cpu_count() or 2, 4), 'num_workers': 1}
    try:
        return WhisperModel(nom, local_files_only=True, **options)
    except Exception:  # noqa: BLE001 — Le backend peut exposer plusieurs types d'erreurs de cache.
        try:
            return WhisperModel(nom, local_files_only=False, **options)
        except Exception:  # noqa: BLE001 — Normaliser l'échec du backend et du téléchargement.
            raise ErreurVocale('Impossible de charger le modèle vocal local. Le premier téléchargement nécessite Internet ; vérifiez la connexion et réessayez.') from None


def preparer_modele():
    with _VERROU:
        _charger_modele(nom_modele())


def transcrire_audio(donnees, langue='fr'):
    verifier_audio(donnees)
    if langue not in LANGUES:
        raise ErreurVocale('Choisissez une langue proposée pour la transcription.')
    nom = nom_modele()
    if not _VERROU.acquire(timeout=3):
        raise ErreurVocale('Une autre transcription est en cours. Patientez puis renvoyez votre enregistrement.')
    try:
        modele = _charger_modele(nom)
        try:
            segments, _ = modele.transcribe(
                BytesIO(donnees), language=langue, task='transcribe', beam_size=1, best_of=1,
                temperature=0.0, vad_filter=True, condition_on_previous_text=False,
                without_timestamps=True, word_timestamps=False,
            )
            morceaux, longueur = [], 0
            for segment in segments:
                texte = str(segment.text).strip()
                longueur += len(texte) + bool(morceaux)
                if longueur > MAX_QUESTION_CHARS:
                    raise ErreurVocale('La transcription dépasse 1 500 caractères. Enregistrez une question plus courte.')
                if texte:
                    morceaux.append(texte)
            texte = ' '.join(morceaux).strip()
        except ErreurVocale:
            raise
        except Exception:  # noqa: BLE001 — Frontière du moteur natif ; jamais afficher ses détails.
            raise ErreurVocale('La transcription a échoué. Réenregistrez une question courte ou utilisez le texte.') from None
    finally:
        _VERROU.release()
    if not texte:
        raise ErreurVocale('Aucune parole reconnue. Parlez plus près du microphone ou choisissez la bonne langue.')
    return texte


def main():
    from dotenv import load_dotenv

    load_dotenv('.env', override=False)
    parser = argparse.ArgumentParser(description='Préparer la transcription vocale locale de Revisiter.')
    parser.add_argument('--preparer', action='store_true', required=True)
    parser.parse_args()
    try:
        print('Préparation du modèle vocal local (téléchargement au premier lancement)…', flush=True)
        preparer_modele()
    except ErreurVocale as erreur:
        raise SystemExit(str(erreur)) from None
    print('Modèle vocal prêt. La transcription fonctionne ensuite sur cet ordinateur.')


if __name__ == '__main__':
    main()
