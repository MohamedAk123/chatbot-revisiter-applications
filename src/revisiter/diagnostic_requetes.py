"""Mesures locales bornées ; ni questions, ni code, ni jetons dans les traces."""

import math
import statistics
from datetime import UTC, datetime
from time import perf_counter

CHAMPS = {'retrieval_ms', 'embedding_ms', 'generation_ms', 'provider_queue_ms', 'queue_ms',
          'first_text_ms', 'total_ms', 'load_ms', 'prompt_eval_ms', 'eval_ms', 'preparation_ms'}


class EmbeddingsMesures:
    def __init__(self, delegate):
        self.delegate, self.total_ms = delegate, 0.0

    def _appeler(self, methode, valeur):
        debut = perf_counter()
        try:
            return getattr(self.delegate, methode)(valeur)
        finally:
            self.total_ms += (perf_counter() - debut) * 1000

    def embed_documents(self, textes):
        return self._appeler('embed_documents', textes)

    def embed_query(self, texte):
        return self._appeler('embed_query', texte)


def consigner_trace(traces, identifiant, statut, mesures):
    statut = statut if statut in {'terminé', 'annulé', 'erreur'} else 'erreur'
    methode = mesures.get('method')
    if methode not in {'static', 'lexical', 'hybrid', 'full_context', 'semantic', 'clarification'}:
        methode = 'semantic'
    ligne = {'id': str(identifiant)[:40], 'created_utc': datetime.now(UTC).isoformat(),
             'status': statut, 'method': methode}
    ligne.update({k: round(float(v), 2) for k, v in mesures.items() if k in CHAMPS
                  and isinstance(v, (float, int)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0})
    return [*traces[-99:], ligne]


def resume_traces(traces):
    valeurs = sorted(t['total_ms'] / 1000 for t in traces if t.get('status') == 'terminé' and 'total_ms' in t)
    return {'requests': len(traces), 'errors': sum(t.get('status') == 'erreur' for t in traces),
            'cancelled': sum(t.get('status') == 'annulé' for t in traces),
            'p50_s': statistics.median(valeurs) if valeurs else None,
            'p95_s': valeurs[math.ceil(.95 * len(valeurs)) - 1] if valeurs else None}
