"""Réutiliser les seuls documents et vecteurs inchangés, dans l’espace autorisé."""

import hashlib
import math

from langchain_core.embeddings import Embeddings

from revisiter.ingestion import Chunk
from revisiter.lecteurs import preparer_document, preparer_extraits
from revisiter.structures import analyser_structure

VERSION = 'preparation-v2'


class IndexIncremental:
    def __init__(self, memoire=None, stockage=None, projet=None, token=None):
        self.memoire = memoire if memoire is not None else {}
        self.stockage, self.projet, self.token = stockage, projet, token
        self.reutilises, self.recalcules = 0, 0

    def preparer(self, nom, texte):
        cle = hashlib.sha256((VERSION + nom + '\x00' + texte).encode()).hexdigest()
        valeur = self.memoire.get(cle)
        if valeur is None and self.stockage and self.projet:
            valeur = self.stockage.lire_cache(self.projet, 'document', cle, self.token)
        if isinstance(valeur, dict) and valeur.get('version') == VERSION:
            try:
                extraits = [Chunk(**c) for c in valeur['chunks']]
                if all(c.source == nom or c.source.startswith(nom + '#cellule') for c in extraits):
                    self.memoire[cle] = valeur
                    self.reutilises += 1
                    return valeur['preview'], extraits, valeur['structure']
            except (KeyError, TypeError):
                pass
        apercu, extraits = preparer_document(nom, texte), preparer_extraits(nom, texte)
        structure = analyser_structure(nom, texte)
        valeur = {'version': VERSION, 'preview': apercu, 'chunks': [vars(c) for c in extraits], 'structure': structure}
        self.memoire[cle] = valeur
        if len(self.memoire) > 600:
            self.memoire.pop(next(iter(self.memoire)))
        if self.stockage and self.projet:
            self.stockage.ecrire_cache(self.projet, 'document', cle, valeur, self.token)
        self.recalcules += 1
        return apercu, extraits, structure


class EmbeddingsPersistants(Embeddings):
    def __init__(self, base, stockage, projet, token, identite_modele):
        self.base, self.stockage, self.projet, self.token = base, stockage, projet, token
        self.identite = 'masked-mean-v2:' + identite_modele
        self.reutilises, self.recalcules = 0, 0

    @staticmethod
    def _valide(v):
        return isinstance(v, list) and 1 <= len(v) <= 4096 and all(isinstance(x, (float, int)) and math.isfinite(x) for x in v)

    def embed_documents(self, texts):
        cles = [hashlib.sha256((self.identite + '\x00' + t).encode()).hexdigest() for t in texts]
        caches = self.stockage.lire_caches(self.projet, 'vecteur', cles, self.token)
        resultats = [caches.get(k) for k in cles]
        manquants = [i for i, v in enumerate(resultats) if not self._valide(v)]
        if manquants:
            nouveaux = self.base.embed_documents([texts[i] for i in manquants])
            if len(nouveaux) != len(manquants) or any(not self._valide(v) for v in nouveaux):
                raise ValueError('L’encodeur a fourni des vecteurs invalides.')
            for i, vecteur in zip(manquants, nouveaux, strict=True):
                resultats[i] = list(vecteur)
            self.stockage.ecrire_caches(self.projet, 'vecteur', {cles[i]: resultats[i] for i in manquants}, self.token)
        self.recalcules += len(manquants)
        self.reutilises += len(texts) - len(manquants)
        return resultats

    def embed_query(self, text):
        return self.base.embed_query(text)
