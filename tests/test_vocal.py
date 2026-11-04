"""Validation des enregistrements et transcription sans réseau ni microphone."""

import sys
import wave
from io import BytesIO
from types import SimpleNamespace

import pytest

from revisiter import vocal


def wav_test(secondes=0.15, frequence=16_000, canaux=1, largeur=2, valeur=500):
    sortie = BytesIO()
    with wave.open(sortie, 'wb') as audio:
        audio.setnchannels(canaux)
        audio.setsampwidth(largeur)
        audio.setframerate(frequence)
        audio.writeframes(int(valeur).to_bytes(largeur, 'little', signed=True) * int(secondes * frequence) * canaux)
    return sortie.getvalue()


@pytest.mark.parametrize('audio', [b'', b'code malveillant', b'RIFF\x01\x02',
    wav_test(valeur=0), wav_test(secondes=0), wav_test(largeur=1, valeur=10),
    wav_test(frequence=4_000), wav_test(canaux=3), wav_test()[:-10]])
def test_audio_invalide_refuse_avant_chargement_du_modele(monkeypatch, audio):
    monkeypatch.setattr(vocal, '_charger_modele', lambda *a: pytest.fail('Le modèle ne doit pas être chargé'))
    with pytest.raises(vocal.ErreurVocale):
        vocal.transcrire_audio(audio)


def test_limites_taille_et_duree():
    with pytest.raises(vocal.ErreurVocale, match='volumineux'):
        vocal.verifier_audio(b'x' * (vocal.MAX_AUDIO_BYTES + 1))
    with pytest.raises(vocal.ErreurVocale, match='60 secondes'):
        vocal.verifier_audio(wav_test(secondes=60.1))
    assert vocal.verifier_audio(wav_test(secondes=60)) == 60


class Moteur:
    def __init__(self, textes=('Quel est le rôle', 'de ce fichier ?')):
        self.textes, self.appels = textes, []

    def transcribe(self, donnees, **options):
        self.appels.append((donnees.read(), options))
        return (SimpleNamespace(text=t) for t in self.textes), None


def test_transcription_locale_francais_vad_et_pas_de_fichier(monkeypatch, tmp_path):
    moteur = Moteur()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('REVISITER_WHISPER_MODEL', 'base')
    monkeypatch.setattr(vocal, '_charger_modele', lambda nom: moteur)
    donnees = wav_test()
    assert vocal.transcrire_audio(donnees) == 'Quel est le rôle de ce fichier ?'
    audio, options = moteur.appels[0]
    assert audio == donnees
    assert options['language'] == 'fr' and options['vad_filter']
    assert options['beam_size'] == 1 and options['condition_on_previous_text'] is False
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('langue', [None, 'ar', 'en'])
def test_les_langues_proposees_ne_sont_pas_traduites(monkeypatch, langue):
    moteur = Moteur()
    monkeypatch.setattr(vocal, '_charger_modele', lambda nom: moteur)
    vocal.transcrire_audio(wav_test(), langue)
    assert moteur.appels[0][1]['language'] == langue
    assert moteur.appels[0][1]['task'] == 'transcribe'


def test_aucune_parole_et_texte_trop_long(monkeypatch):
    for textes, message in [(('', ' '), 'Aucune parole'), (('x' * 1501,), '1 500')]:
        monkeypatch.setattr(vocal, '_charger_modele', lambda nom, textes=textes: Moteur(textes))
        with pytest.raises(vocal.ErreurVocale, match=message):
            vocal.transcrire_audio(wav_test())


def test_erreur_dans_le_generateur_est_presentee_sans_details_et_libere_le_verrou(monkeypatch):
    class Echec:
        def transcribe(self, *args, **kwargs):
            def segments():
                raise RuntimeError('SECRET_ET_CHEMIN_PRIVE')
                yield
            return segments(), None

    monkeypatch.setattr(vocal, '_charger_modele', lambda nom: Echec())
    with pytest.raises(vocal.ErreurVocale, match='La transcription a échoué') as erreur:
        vocal.transcrire_audio(wav_test())
    assert 'SECRET' not in str(erreur.value)
    assert not vocal._VERROU.locked()


def test_modele_et_langue_non_autorises(monkeypatch):
    with pytest.raises(vocal.ErreurVocale, match='langue'):
        vocal.transcrire_audio(wav_test(), 'xx')
    monkeypatch.setenv('REVISITER_WHISPER_MODEL', '../../modele-inconnu')
    with pytest.raises(vocal.ErreurVocale, match='tiny, base ou small'):
        vocal.transcrire_audio(wav_test())


def test_cache_du_modele_cpu_int8_sans_audio_dans_le_cache(monkeypatch):
    appels = []
    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(
        WhisperModel=lambda *a, **kw: appels.append((a, kw)) or object()))
    vocal._charger_modele.cache_clear()
    try:
        un = vocal._charger_modele('base')
        assert vocal._charger_modele('base') is un
        assert len(appels) == 1
        assert appels[0][1]['device'] == 'cpu' and appels[0][1]['compute_type'] == 'int8'
        assert appels[0][1]['local_files_only'] is True
        assert vocal._charger_modele.cache_info().maxsize == 1
    finally:
        vocal._charger_modele.cache_clear()


def test_dependance_absente_et_telechargement_impossible(monkeypatch):
    vocal._charger_modele.cache_clear()
    try:
        monkeypatch.setitem(sys.modules, 'faster_whisper', None)
        with pytest.raises(vocal.ErreurVocale, match='pas installée'):
            vocal._charger_modele('base')

        def echec(*a, **kw):
            raise OSError('adresse privée')

        monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=echec))
        with pytest.raises(vocal.ErreurVocale, match='premier téléchargement'):
            vocal._charger_modele('base')
    finally:
        vocal._charger_modele.cache_clear()
