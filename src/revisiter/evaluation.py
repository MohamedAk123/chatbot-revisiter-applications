"""Évaluer les règles de réponse localement ; un score lexical n’est pas un jugement sémantique."""

import argparse
import json
import math
import re
import statistics
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from revisiter.jeux_evaluation import jeu_reference
from revisiter.lecteurs import preparer_extraits
from revisiter.robustesse import reference_couverte
from revisiter.workflow import build_question_graph, iterer_reponse


class EncodeurInterdit:
    def embed_documents(self, texts):
        raise RuntimeError('Ce jeu borné ne nécessite pas d’encodeur.')

    def embed_query(self, text):
        raise RuntimeError('Ce jeu borné ne nécessite pas d’encodeur.')


class ModeleInterdit:
    def stream(self, messages):
        raise RuntimeError('Appel au modèle désactivé pour cet essai.')


def evaluer_cas(cas, modele=None):
    debut = perf_counter()
    if cas['kind'] == 'llm' and modele is None:
        return {'id': cas['id'], 'status': 'non_execute', 'checks_passed': None, 'review_required': True}
    documents = cas['documents']
    extraits = [p for nom, code in documents.items() for p in preparer_extraits(nom, code)]
    resultat, premier, morceaux = {}, None, []
    try:
        graphe = build_question_graph(extraits, EncodeurInterdit(), modele or ModeleInterdit(),
            top_k=4, retrieval_mode='auto', documents=documents)
        for fragment in iterer_reponse(graphe, {'question': cas['question'], 'request_started': debut}, resultat):
            if fragment and premier is None:
                premier = perf_counter() - debut
            morceaux.append(fragment)
        reponse = ''.join(morceaux)
        texte_ok = all(m.casefold() in reponse.casefold() for m in cas.get('contains', []))
        sources_ok = all(reference_couverte(ref, resultat.get('sources', [])) for ref in cas.get('sources', []))
        citations = re.findall(r'\[([^\]\n]+:L\d+(?:-L\d+)?)\]', reponse)
        citations_ok = (not cas.get('sources') or bool(citations)) and all(reference_couverte(ref, resultat.get('sources', [])) for ref in citations)
        return {'id': cas['id'], 'status': 'a_relire' if cas['kind'] == 'llm' else 'reussi' if texte_ok and sources_ok and citations_ok else 'echec',
            'checks_passed': texte_ok and sources_ok and citations_ok, 'citations_present': bool(citations), 'review_required': cas['kind'] == 'llm',
            'answer': reponse, 'sources': resultat.get('sources', []), 'rubric': cas['rubric'],
            'first_text_s': premier, 'total_s': perf_counter() - debut, 'metrics': resultat.get('metrics', {})}
    except (ValueError, RuntimeError, OSError, ConnectionError, TypeError) as erreur:
        return {'id': cas['id'], 'status': 'erreur', 'checks_passed': False, 'review_required': True,
            'error': type(erreur).__name__, 'total_s': perf_counter() - debut}


def rapport(resultats):
    executes = [r for r in resultats if r['status'] != 'non_execute']
    durees = sorted(r['total_s'] for r in executes if 'total_s' in r)
    return {'created_utc': datetime.now(UTC).isoformat(), 'cases': resultats, 'executed': len(executes),
        'checks_passed': sum(r.get('checks_passed') is True for r in executes),
        'human_review_pending': sum(r.get('review_required') is True for r in executes),
        'p50_s': statistics.median(durees) if durees else None,
        'p95_s': durees[math.ceil(len(durees) * .95) - 1] if durees else None,
        'limits': 'Vérifications de mots et plages de sources. Les raisonnements LLM nécessitent une revue humaine avec la rubrique fournie.'}


def main():
    parser = argparse.ArgumentParser(description='Évaluation locale du chatbot sur 30 questions pédagogiques.')
    parser.add_argument('--llm', action='store_true', help='Inclure les 15 cas interprétatifs avec Ollama local.')
    parser.add_argument('--output', default='evaluation_revisiter.json')
    args = parser.parse_args()
    modele = None
    if args.llm:
        from revisiter.config import load_settings
        from revisiter.ollama_client import create_chat_model
        config = load_settings()
        if config.mode != 'local':
            raise SystemExit('Ce banc d’essai utilise exclusivement Ollama local.')
        modele = create_chat_model(config)
    resultats = [evaluer_cas(c, modele) for c in jeu_reference()]
    Path(args.output).write_text(json.dumps(rapport(resultats), ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'{len(resultats)} cas consignés dans {args.output}. Les réponses LLM restent à relire.')


if __name__ == '__main__':
    main()
