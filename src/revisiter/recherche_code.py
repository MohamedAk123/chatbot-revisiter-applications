"""Repérage lexical du code, puis recherche sémantique ciblée si nécessaire."""

import math
import re
import unicodedata
from collections import Counter
from time import perf_counter

from revisiter.calculs_python import calcul_python
from revisiter.relations import analyser_impact, construire_relations, texte_impact
from revisiter.roles_fichiers import est_demande_roles, roles_fichiers
from revisiter.schemas_appels import est_demande_appels, schema_appels
from revisiter.structures import appels_nommes, inventaire

STOP = {"le", "la", "les", "de", "des", "du", "un", "une", "et", "ou", "en", "est", "que", "qui", "quel", "quelle", "quels", "quelles", "dans", "sur", "au", "aux", "pour", "avec", "ce", "ces", "se", "son", "sa", "ses", "the", "a", "an", "of", "to", "is", "in", "explique", "donne", "moi", "mon", "ma", "mes", "fichier", "fichiers", "projet", "code"}


def normaliser(texte):
    return "".join(c for c in unicodedata.normalize("NFKD", texte.casefold()) if not unicodedata.combining(c))


def tokens(texte):
    separe = re.sub(r"([a-z])([A-Z])", r"\1 \2", texte)
    mots = re.findall(r"[\w]+", normaliser(texte + " " + separe))
    return [m for mot in mots for m in (mot, *mot.split("_")) if len(m) > 1 and m not in STOP]


class IndexCode:
    def __init__(self, chunks, embeddings, documents=None, structures=None):
        self.chunks = tuple(chunks)
        self.embeddings = embeddings
        self.documents = documents or {}
        self.structures = structures
        self.vecteurs = {}
        self.semantic_failed = False
        self.relations = None
        self.compteurs = [Counter(tokens(c.source + " " + c.text)) for c in chunks]
        self.frequences = Counter(m for compteur in self.compteurs for m in compteur)
        self.moyenne = sum(sum(c.values()) for c in self.compteurs) / max(1, len(chunks))

    def schema_appels(self, question, budget):
        if not self.documents or not est_demande_appels(question):
            return None
        debut = perf_counter()
        if self.relations is None:
            self.relations = construire_relations(self.documents, self.structures)
        resultat = schema_appels(question, self.relations, self.documents, budget)
        if resultat is not None:
            resultat['metrics'] = {'method': 'static', 'retrieval_ms': (perf_counter() - debut) * 1000}
        return resultat

    def rechercher(self, question, top_k, budget, mode="auto", *, static=True):
        debut = perf_counter()
        q = normaliser(question)
        if static and self.documents:
            calcul = calcul_python({'texte': question, 'normalisee': q}, self.documents, budget)
            if calcul is not None:
                return {**calcul, 'metrics': {'method': 'static', 'calculation_verified': True,
                        'retrieval_ms': (perf_counter() - debut) * 1000}}
        if static and self.documents and est_demande_roles(q):
            resultat = roles_fichiers(self.documents, question, budget, self.structures)
            if resultat is not None:
                texte, sources, contexte = resultat
                return {"context": contexte, "sources": sources, "direct_answer": texte,
                        "metrics": {"method": "static", "retrieval_ms": (perf_counter() - debut) * 1000}}
        if static and self.documents and (re.search(r'\b(impact|affect\w*|appelants?)\b', q) or
                re.search(r'\bconcern\w*\b', q) and re.search(r'\b(modifi\w*|chang\w*)\b', q)):
            if self.relations is None:
                self.relations = construire_relations(self.documents, self.structures)
            candidats = [n for n in self.relations['nodes'].values() if n['kind'] == 'function'
                and re.search(r'(?<!\w)' + re.escape(n['name'].split('.')[-1]) + r'(?!\w)', question)]
            precises = [n for n in candidats if n['file'].casefold() in question.casefold()]
            if precises:
                candidats = precises
            if candidats:
                if len(candidats) == 1:
                    impact = analyser_impact(self.relations, candidats[0]['id'])
                    texte = texte_impact(impact)
                    sources = list(dict.fromkeys([candidats[0]['reference']] + [n['call_reference'] for n in impact['callers']] + [l['reference'] for l in impact['importers']]))
                else:
                    sources = [n['reference'] for n in candidats[:10]]
                    texte = 'Plusieurs déclarations correspondent. Précisez le fichier ou utilisez la cartographie :\n\n' + '\n'.join(sources)
                contexte = '\n\n'.join(f'[{r}]' for r in sources)
                return {'context': contexte, 'sources': sources, 'direct_answer': texte,
                    'metrics': {'method': 'static', 'retrieval_ms': (perf_counter() - debut) * 1000}}
        appel = re.search(r"\bappelle(?:nt)?\s+(?:la\s+fonction\s+)?`?([a-z_]\w*)", question, re.IGNORECASE)
        if static and self.documents and appel and re.search(r"\bfichiers?\b|\bqui\s+appelle", q) and not re.search(r"\b(pourquoi|comment|but|role|risques?|modifi\w*)\b", q):
            texte, sources, contexte = appels_nommes(self.documents, appel[1], budget, analyses=self.structures)
            return {"context": contexte, "sources": sources, "direct_answer": texte,
                    "metrics": {"method": "static", "retrieval_ms": (perf_counter() - debut) * 1000}}
        if static and self.documents and re.search(r"\b(liste|inventaire|enumere|noms)\b", q) and re.search(r"\b(fonctions?|methodes?|classes?)\b", q) and not re.search(r"\b(explique|role|fonctionnement|risques?|problemes?|erreurs?|tests?|modifi\w*|corrig\w*|parametres?|retourn\w*)\b", q):
            texte, sources, contexte = inventaire(self.documents, "class" if "classe" in q else "function", budget=budget, analyses=self.structures)
            return {"context": contexte, "sources": sources, "direct_answer": texte,
                    "metrics": {"method": "static", "retrieval_ms": (perf_counter() - debut) * 1000}}
        mots = Counter(tokens(question))
        scores = []
        for i, (chunk, compteur) in enumerate(zip(self.chunks, self.compteurs, strict=True)):
            longueur = sum(compteur.values())
            score = sum((math.log(1 + (len(self.chunks) - self.frequences[m] + .5) / (self.frequences[m] + .5))
                         * compteur[m] * 2.2 / (compteur[m] + 1.2 * (.25 + .75 * longueur / max(1, self.moyenne))))
                        for m in mots if compteur[m])
            if normaliser(chunk.source) in q:
                score += 20
            scores.append((i, score))
        lexicaux = [i for i, _ in sorted(scores, key=lambda x: x[1], reverse=True)]
        ancres = re.findall(r"\b[a-zA-Z_]\w*_\w+\b", question) + re.findall(r"`([\w.]+)`", question)
        ancres = [normaliser(a) for a in ancres if any(re.search(r"(?<!\w)" + re.escape(normaliser(a)) + r"(?!\w)", normaliser(c.text)) for c in self.chunks)]
        fichiers_cibles = {c.source for c in self.chunks if normaliser(c.source) in q}
        explicite = bool(fichiers_cibles or ancres)
        methode, avertissement = "lexical", ""
        complet = "\n\n".join(f"[{c.reference}]\n{c.text}" for c in self.chunks)
        if not explicite and len(complet) <= budget:
            ordre = list(range(len(self.chunks)))
            methode = "full_context"
        elif explicite or mode == "lexical":
            exacts = [i for i in lexicaux if self.chunks[i].source in fichiers_cibles or any(
                re.search(r"(?<!\w)" + re.escape(a) + r"(?!\w)", normaliser(self.chunks[i].text)) for a in ancres)]
            voisins = [j for i in exacts for j in (i - 1, i + 1) if 0 <= j < len(self.chunks) and self.chunks[j].source == self.chunks[i].source]
            ordre = list(dict.fromkeys(exacts + voisins)) if exacts else ([i for i in lexicaux if scores[i][1] > 0] or lexicaux)
        else:
            candidats = lexicaux[:32]
            try:
                if self.semantic_failed:
                    raise RuntimeError("Encodeur indisponible pour cet index.")
                manquants = [i for i in candidats if i not in self.vecteurs]
                if manquants:
                    nouveaux = self.embeddings.embed_documents([self.chunks[i].text for i in manquants])
                    self.vecteurs.update(zip(manquants, nouveaux, strict=True))
                vecteurs = [self.vecteurs[i] for i in candidats]
                requete = self.embeddings.embed_query(question)
                semantiques = sorted(zip(candidats, vecteurs, strict=True), key=lambda x: sum(
                    a * b for a, b in zip(requete, x[1], strict=True)), reverse=True)
                rangs = {i: 1 / (60 + r) for r, i in enumerate(lexicaux, start=1)}
                for r, (i, _) in enumerate(semantiques, start=1):
                    rangs[i] += 1 / (60 + r)
                ordre = sorted(candidats, key=lambda i: rangs[i], reverse=True)
                methode = "hybrid"
            except (ImportError, OSError, RuntimeError, ValueError):
                self.semantic_failed = True
                ordre = lexicaux
                avertissement = "Recherche lexicale utilisée : encodeur sémantique indisponible."
        blocs, sources = [], []
        for i in ordre:
            chunk = self.chunks[i]
            bloc = f"[{chunk.reference}]\n{chunk.text}"
            if len("\n\n".join([*blocs, bloc])) > budget:
                continue
            blocs.append(bloc)
            sources.append(chunk.reference)
            if methode != "full_context" and len(sources) >= top_k:
                break
        if not sources and self.chunks:
            raise ValueError("Aucun passage ne tient dans le budget de contexte.")
        return {"context": "\n\n".join(blocs), "sources": sources, "metrics": {
            "method": methode, "retrieval_ms": (perf_counter() - debut) * 1000,
            "total_chunks": len(self.chunks), "selected_chunks": len(sources), "warning": avertissement,
        }}
