"""Collecte bornée du code proposé ; aucun code n’est exécuté."""

import re
from time import perf_counter

from langchain_core.messages import HumanMessage, SystemMessage


def generer_code(modele, consigne, demande):
    messages = [SystemMessage(content=consigne), HumanMessage(content=demande)]
    morceaux, taille, debut = [], 0, perf_counter()
    flux = modele.stream(messages)
    try:
        for fragment in flux:
            if perf_counter() - debut > 120:
                raise ValueError('La génération dépasse le délai prévu. La proposition incomplète est rejetée.')
            if getattr(fragment, 'response_metadata', {}).get('done_reason') == 'length':
                raise ValueError('La génération est tronquée. Réduisez la portée des tests.')
            if not isinstance(fragment.content, str):
                raise ValueError('Le modèle n’a pas fourni du texte.')  # noqa: TRY004
            taille += len(fragment.content)
            if taille > 16000:
                raise ValueError('La proposition dépasse la taille prévue.')
            morceaux.append(fragment.content)
    finally:
        if callable(getattr(flux, 'close', None)):
            flux.close()
    code = ''.join(morceaux)
    bloc = re.fullmatch(r'\s*```[\w+#.-]*\n(.*?)\n```\s*', code, flags=re.DOTALL)
    if bloc:
        code = bloc[1]
    if not code.strip():
        raise ValueError('Le modèle a fourni une proposition vide.')
    return code
