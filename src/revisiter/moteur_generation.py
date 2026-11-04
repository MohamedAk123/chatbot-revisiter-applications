"""Une génération à la fois, annulation du transport asynchrone et attente bornée."""

import asyncio
from collections import deque
from concurrent.futures import TimeoutError as AttenteAsync
from contextlib import contextmanager
from queue import Empty, Queue
from threading import Condition, Event, Lock, Thread
from time import monotonic


class GenerationAnnulee(ValueError):
    pass


class ControleGeneration:
    def __init__(self, delai=180):
        self.annulation = Event()
        self.debut = monotonic()
        self.delai = delai
        self.phase = 'en attente'

    def verifier(self):
        if self.annulation.is_set():
            raise GenerationAnnulee('Génération annulée. Aucun message incomplet n’a été enregistré.')
        if monotonic() - self.debut > self.delai:
            raise TimeoutError('Le délai total de la requête est dépassé.')


class Ordonnanceur:
    def __init__(self):
        self.condition = Condition()
        self.attente = deque()
        self.occupe = False

    @contextmanager
    def reserver(self, controle):
        ticket = object()
        debut = monotonic()
        with self.condition:
            self.attente.append(ticket)
            try:
                while self.occupe or self.attente[0] is not ticket:
                    controle.verifier()
                    self.condition.wait(.05)
                controle.verifier()
                self.attente.popleft()
                self.occupe = True
            except BaseException:
                self.attente.remove(ticket)
                self.condition.notify_all()
                raise
        try:
            yield (monotonic() - debut) * 1000
        finally:
            with self.condition:
                self.occupe = False
                self.condition.notify_all()


ORDONNANCEUR = Ordonnanceur()
_BOUCLE = None
_VERROU_BOUCLE = Lock()


def _boucle_transport():
    global _BOUCLE
    with _VERROU_BOUCLE:
        if _BOUCLE is None:
            boucle = asyncio.new_event_loop()
            Thread(target=boucle.run_forever, name='revisiter-transport', daemon=True).start()
            _BOUCLE = boucle
        return _BOUCLE


def _appel_annulable(appel, controle):
    termine = Event()

    async def executer():
        try:
            return await appel
        finally:
            termine.set()

    futur = asyncio.run_coroutine_threadsafe(executer(), _boucle_transport())
    try:
        while True:
            controle.verifier()
            try:
                return futur.result(timeout=.05)
            except AttenteAsync:
                if futur.done():
                    return futur.result()
                continue
    finally:
        if not futur.done():
            futur.cancel()
        termine.wait(2)


def flux_annulable(flux, controle):
    evenements, termine = Queue(), Event()

    async def produire():
        taille = 0
        try:
            async for fragment in flux:
                controle.verifier()
                taille += len(fragment.content) if isinstance(fragment.content, str) else 0
                if taille > 48_000:
                    raise ValueError('La réponse dépasse la limite de texte prévue.')
                evenements.put((True, fragment))
        except BaseException as erreur:
            evenements.put((False, erreur))
            raise
        finally:
            try:
                if callable(getattr(flux, 'aclose', None)):
                    await flux.aclose()
            finally:
                evenements.put((None, None))
                termine.set()

    futur = asyncio.run_coroutine_threadsafe(produire(), _boucle_transport())
    try:
        while True:
            controle.verifier()
            try:
                statut, valeur = evenements.get(timeout=.05)
            except Empty:
                continue
            if statut is None:
                break
            if statut is False:
                raise valeur
            yield valeur
    finally:
        if not futur.done():
            futur.cancel()
        termine.wait(2)


class ModelePlanifie:
    def __init__(self, modele, controle=None, ordonnanceur=None):
        self.modele = modele
        self.controle = controle
        self.ordonnanceur = ordonnanceur or ORDONNANCEUR
        self.timings = {}

    def __getattr__(self, nom):
        return getattr(self.modele, nom)

    def avec_controle(self, controle):
        return ModelePlanifie(self.modele, controle, self.ordonnanceur)

    def with_structured_output(self, *args, **kwargs):
        enfant = ModelePlanifie(self.modele.with_structured_output(*args, **kwargs), self.controle, self.ordonnanceur)
        enfant.timings = self.timings
        return enfant

    def invoke(self, messages, **kwargs):
        controle = self.controle or ControleGeneration()
        controle.phase = 'attente du moteur'
        with self.ordonnanceur.reserver(controle) as attente:
            self.timings.clear()
            self.timings['provider_queue_ms'] = attente
            controle.phase = 'génération'
            controle.verifier()
            if self.controle and callable(getattr(self.modele, 'ainvoke', None)):
                return _appel_annulable(self.modele.ainvoke(messages, **kwargs), controle)
            resultat = self.modele.invoke(messages, **kwargs)
            controle.verifier()
            return resultat

    def stream(self, messages, **kwargs):
        controle = self.controle or ControleGeneration()
        controle.phase = 'attente du moteur'
        with self.ordonnanceur.reserver(controle) as attente:
            self.timings.clear()
            self.timings['provider_queue_ms'] = attente
            controle.phase = 'génération'
            if self.controle and callable(getattr(self.modele, 'astream', None)):
                flux = flux_annulable(self.modele.astream(messages, **kwargs), controle)
            elif not callable(getattr(self.modele, 'stream', None)):
                resultat = self.modele.invoke(messages, **kwargs)
                controle.verifier()
                yield resultat
                return
            else:
                flux = self.modele.stream(messages, **kwargs)
            try:
                for fragment in flux:
                    controle.verifier()
                    for cle in ('load_duration', 'prompt_eval_duration', 'eval_duration'):
                        valeur = getattr(fragment, 'response_metadata', {}).get(cle)
                        if isinstance(valeur, (float, int)) and valeur >= 0:
                            self.timings[cle.removesuffix('_duration') + '_ms'] = valeur / 1_000_000
                    yield fragment
            finally:
                if callable(getattr(flux, 'close', None)):
                    flux.close()


def planifier_modele(modele, controle=None):
    if isinstance(modele, ModelePlanifie):
        return modele.avec_controle(controle) if controle else modele
    return ModelePlanifie(modele, controle)
