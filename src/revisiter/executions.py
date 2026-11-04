"""Travaux bornés, privés à une session ; aucun état Streamlit dans les travailleurs."""

from concurrent.futures import CancelledError, ThreadPoolExecutor
from concurrent.futures import TimeoutError as AttenteDepassee
from copy import deepcopy
from functools import lru_cache
from threading import RLock
from time import monotonic
from uuid import uuid4

from revisiter.moteur_generation import ControleGeneration, GenerationAnnulee
from revisiter.robustesse import message_erreur

TERMINES = {'terminé', 'annulé', 'erreur'}


class Executions:
    def __init__(self, maximum=6):
        self.verrou = RLock()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='revisiter')
        self.travaux = {}
        self.maximum = maximum

    def _nettoyer(self):
        anciens = [identifiant for identifiant, t in self.travaux.items()
                   if t['status'] in TERMINES and monotonic() - t['created'] > 600]
        for identifiant in anciens:
            del self.travaux[identifiant]
        finis = [i for i, t in self.travaux.items() if t['status'] in TERMINES]
        for identifiant in finis[:max(0, len(self.travaux) - 60)]:
            del self.travaux[identifiant]

    def lancer(self, proprietaire, contexte, question, fonction):
        with self.verrou:
            self._nettoyer()
            actifs = [t for t in self.travaux.values() if t['status'] not in TERMINES]
            if any(t['owner'] == proprietaire for t in actifs):
                raise ValueError('Une requête de cette session est encore en cours.')
            if len(actifs) >= self.maximum:
                raise ValueError('La file d’attente est pleine. Réessayez après une réponse.')
            identifiant = str(uuid4())
            travail = {'id': identifiant, 'owner': proprietaire, 'context': contexte, 'question': question,
                       'created': monotonic(), 'status': 'en attente', 'text': '', 'result': None,
                       'error': None, 'control': ControleGeneration(), 'fn': fonction}
            self.travaux[identifiant] = travail
            travail['future'] = self.pool.submit(self._executer, identifiant)
            return identifiant

    def _executer(self, identifiant):
        with self.verrou:
            t = self.travaux[identifiant]
            t['status'] = 'en cours'
            t['started'] = monotonic()
        controle = t['control']

        def emettre(texte):
            controle.verifier()
            if not isinstance(texte, str):
                raise TypeError('Le texte reçu est invalide.')
            with self.verrou:
                if len(t['text']) + len(texte) > 48_000:
                    raise ValueError('La réponse dépasse la limite de texte prévue.')
                t['text'] += texte

        try:
            controle.verifier()
            resultat = t['fn'](controle, emettre)
            controle.verifier()
            with self.verrou:
                t['result'] = resultat
                t['status'] = 'terminé'
        except GenerationAnnulee:
            with self.verrou:
                t['status'], t['text'], t['result'] = 'annulé', '', None
        except Exception as erreur:  # noqa: BLE001 — Frontière du travail isolé de l'interface.
            with self.verrou:
                t['status'], t['error'], t['text'] = 'erreur', message_erreur(erreur), ''
        finally:
            with self.verrou:
                t['finished'] = monotonic()
                t['fn'] = None

    def _autoriser(self, identifiant, proprietaire):
        t = self.travaux.get(identifiant)
        if t is None or t['owner'] != proprietaire:
            raise PermissionError('Cette requête n’appartient pas à la session.')
        return t

    def lire(self, identifiant, proprietaire):
        with self.verrou:
            t = self._autoriser(identifiant, proprietaire)
            retour = {k: deepcopy(t.get(k)) for k in ('id', 'context', 'question', 'status', 'text', 'result', 'error')}
            retour['phase'] = t['control'].phase
            retour['queue_ms'] = (t.get('started', monotonic()) - t['created']) * 1000
            retour['elapsed_ms'] = (t.get('finished', monotonic()) - t['created']) * 1000
            return retour

    def attendre(self, identifiant, proprietaire, secondes=.3):
        with self.verrou:
            futur = self._autoriser(identifiant, proprietaire)['future']
        try:
            futur.result(timeout=secondes)
        except (AttenteDepassee, CancelledError):
            pass
        return self.lire(identifiant, proprietaire)

    def annuler(self, identifiant, proprietaire):
        with self.verrou:
            t = self._autoriser(identifiant, proprietaire)
            if t['status'] not in TERMINES:
                t['control'].annulation.set()
                if t['future'].cancel():
                    t['status'], t['finished'] = 'annulé', monotonic()
                    t['fn'] = None
                else:
                    t['status'] = 'annulation demandée'

    def oublier(self, identifiant, proprietaire):
        with self.verrou:
            t = self._autoriser(identifiant, proprietaire)
            if t['status'] not in TERMINES:
                raise ValueError('La requête est encore active.')
            del self.travaux[identifiant]

    def fermer(self):
        with self.verrou:
            for t in self.travaux.values():
                t['control'].annulation.set()
        self.pool.shutdown(wait=True, cancel_futures=True)


@lru_cache(maxsize=1)
def gestionnaire_executions():
    return Executions()
