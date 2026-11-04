import asyncio
from threading import Event, Thread

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from revisiter.diagnostic_requetes import consigner_trace, resume_traces
from revisiter.executions import Executions
from revisiter.moteur_generation import (
    ControleGeneration,
    GenerationAnnulee,
    ModelePlanifie,
    Ordonnanceur,
)


def test_requetes_privees_file_bornee_et_annulation_en_attente():
    gestionnaire = Executions(maximum=3)
    liberation = Event()
    entres = [Event(), Event()]
    execute = []

    def travail(indice):
        def fonction(controle, emettre):
            entres[indice].set()
            while not liberation.wait(.02):
                controle.verifier()
            emettre('ok')
            return {'answer': 'ok'}
        return fonction

    try:
        un = gestionnaire.lancer('session-a', 'projet-a', 'question a', travail(0))
        deux = gestionnaire.lancer('session-b', 'projet-b', 'question b', travail(1))
        assert all(e.wait(2) for e in entres)
        trois = gestionnaire.lancer('session-c', 'projet-c', 'question c', lambda *a: execute.append('c'))
        with pytest.raises(PermissionError):
            gestionnaire.lire(un, 'session-b')
        with pytest.raises(PermissionError):
            gestionnaire.annuler(un, 'session-b')
        with pytest.raises(ValueError, match='session'):
            gestionnaire.lancer('session-a', 'autre', 'doublon', travail(0))
        with pytest.raises(ValueError, match='pleine'):
            gestionnaire.lancer('session-d', 'd', 'd', travail(0))
        gestionnaire.annuler(trois, 'session-c')
        assert gestionnaire.lire(trois, 'session-c')['status'] == 'annulé' and not execute
        liberation.set()
        assert gestionnaire.attendre(un, 'session-a', 2)['result']['answer'] == 'ok'
        assert gestionnaire.attendre(deux, 'session-b', 2)['status'] == 'terminé'
    finally:
        liberation.set()
        gestionnaire.fermer()


def test_annulation_discarde_texte_partiel_et_resultat():
    gestionnaire = Executions()
    pret = Event()

    def travail(controle, emettre):
        emettre('texte partiel')
        pret.set()
        while not controle.annulation.wait(.02):
            pass
        return {'answer': 'ne doit pas être enregistré'}

    try:
        identifiant = gestionnaire.lancer('a', 'projet', 'question', travail)
        assert pret.wait(2)
        gestionnaire.annuler(identifiant, 'a')
        resultat = gestionnaire.attendre(identifiant, 'a', 2)
        assert resultat['status'] == 'annulé' and resultat['result'] is None and resultat['text'] == ''
        gestionnaire.oublier(identifiant, 'a')
        with pytest.raises(PermissionError):
            gestionnaire.lire(identifiant, 'a')
    finally:
        gestionnaire.fermer()


def test_annulation_async_avant_premier_fragment_ferme_le_transport():
    pret, ferme, termine = Event(), Event(), Event()
    controle = ControleGeneration()
    erreurs = []

    class Modele:
        async def astream(self, messages):
            try:
                pret.set()
                await asyncio.sleep(30)
                yield AIMessageChunk(content='trop tard')
            finally:
                ferme.set()

    modele = ModelePlanifie(Modele(), controle, Ordonnanceur())

    def consommer():
        try:
            list(modele.stream([]))
        except GenerationAnnulee as erreur:
            erreurs.append(erreur)
        finally:
            termine.set()

    fil = Thread(target=consommer, daemon=True)
    fil.start()
    try:
        assert pret.wait(3)
        controle.annulation.set()
        assert termine.wait(3) and ferme.wait(1) and erreurs
        assert not modele.ordonnanceur.occupe
    finally:
        controle.annulation.set()
        fil.join(timeout=3)


def test_clients_async_reutilisent_la_meme_boucle_et_invoke_sans_stream_marche():
    boucles = []

    class Async:
        async def astream(self, messages):
            boucles.append(asyncio.get_running_loop())
            yield AIMessageChunk(content='ok')

    for _ in range(2):
        modele = ModelePlanifie(Async(), ControleGeneration(), Ordonnanceur())
        assert ''.join(m.content for m in modele.stream([])) == 'ok'
    assert boucles[0] is boucles[1]

    class SansStream:
        def invoke(self, messages):
            return AIMessage(content='sans streaming')

    modele = ModelePlanifie(SansStream(), ControleGeneration(), Ordonnanceur())
    assert next(iter(modele.stream([]))).content == 'sans streaming'


def test_une_seule_generation_a_la_fois_et_attente_annulable():
    ordonnanceur, controle = Ordonnanceur(), ControleGeneration()
    resultat, termine = [], Event()

    def attendre():
        try:
            with ordonnanceur.reserver(controle):
                resultat.append('interdit')
        except GenerationAnnulee:
            resultat.append('annulé')
        finally:
            termine.set()

    with ordonnanceur.reserver(ControleGeneration()):
        fil = Thread(target=attendre, daemon=True)
        fil.start()
        controle.annulation.set()
        assert termine.wait(2)
    fil.join(2)
    assert resultat == ['annulé'] and not ordonnanceur.occupe and not ordonnanceur.attente


def test_traces_sans_contenu_bornees_et_sans_valeurs_non_finies():
    traces = []
    for i in range(105):
        traces = consigner_trace(traces, str(i), 'terminé', {'method': 'static', 'total_ms': 20,
                                'question': 'SECRET', 'context': 'CODE', 'embedding_ms': float('nan')})
    assert len(traces) == 100 and 'SECRET' not in str(traces) and 'CODE' not in str(traces)
    assert 'embedding_ms' not in traces[-1]
    assert resume_traces(traces)['p95_s'] == .02


def test_exception_timeout_du_fournisseur_async_n_est_pas_une_attente_infinie():
    class Modele:
        async def ainvoke(self, messages):
            raise TimeoutError('délai du fournisseur fictif')

    modele = ModelePlanifie(Modele(), ControleGeneration(), Ordonnanceur())
    with pytest.raises(TimeoutError, match='fournisseur fictif'):
        modele.invoke([])
    assert not modele.ordonnanceur.occupe
