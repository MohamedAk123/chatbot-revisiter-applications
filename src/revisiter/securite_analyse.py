"""Repérer des secrets et instructions suspectes sans conserver leurs valeurs."""

import re

MARQUEUR = '[SECRET_MASQUE]'
SECRETS = [
    ('affectation sensible', re.compile(r'''(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|mot_de_passe|client[_-]?secret|secret[_-]?key)\b["']?\s*[:=]\s*["'](?P<valeur>[^"'\r\n]{6,})''')),
    ('jeton connu', re.compile(r'(?P<valeur>\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[A-Z0-9]{16})\b)')),
    ('autorisation Bearer', re.compile(r'(?i)\bBearer\s+(?P<valeur>[A-Za-z0-9._~-]{15,})')),
    ('clé privée', re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----(?P<valeur>[\s\S]+?)-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')),
]
INSTRUCTIONS = re.compile(
    r'(?i)(?:ignore|disregard|oublie|ignorez|annule)\s+(?:(?:all|the|toutes?|les|des)\s+){0,3}'
    r'(?:previous|prior|précédentes?|precedentes?|system|système)\s+(?:instructions?|prompts?|consignes?)'
    r'|(?:ignore|ignorez|oublie|annule)\s+(?:(?:toutes?|les|des)\s+){0,3}(?:instructions?|consignes?)\s+(?:précédentes?|precedentes?)'
    r'|(?:reveal|affiche|révèle|revele)\s+(?:(?:the|le|ton|your)\s+)?(?:system prompt|prompt système)'
    r'|(?:envoie|exfiltrat\w*)\s+(?:les?\s+)?(?:clés?|secrets?|tokens?)\s+(?:à|a|vers|to)')


def examiner_fichier(nom, contenu):
    regions, detections = [], []
    for genre, motif in SECRETS:
        for match in motif.finditer(contenu):
            valeur = match.group('valeur')
            if MARQUEUR in valeur or valeur.upper().startswith(('REMPLACE_', 'YOUR_', 'EXAMPLE_', 'PLACEHOLDER')):
                continue
            debut, fin = match.span('valeur')
            regions.append((debut, fin))
            if len(detections) < 200:
                detections.append({'file': nom, 'line': contenu.count('\n', 0, debut) + 1, 'kind': genre})
    fusion = []
    for debut, fin in sorted(regions):
        if fusion and debut <= fusion[-1][1]:
            fusion[-1] = (fusion[-1][0], max(fin, fusion[-1][1]))
        else:
            fusion.append((debut, fin))
    masque = contenu
    for debut, fin in reversed(fusion):
        masque = masque[:debut] + MARQUEUR + '\n' * contenu[debut:fin].count('\n') + masque[fin:]
    instructions = [{'file': nom, 'line': contenu.count('\n', 0, m.start()) + 1,
                     'kind': 'instruction suspecte à examiner'} for m in INSTRUCTIONS.finditer(contenu)][:100]
    return {'text': masque, 'secrets': detections, 'secret_count': len(fusion), 'instructions': instructions}


def proteger_documents(documents, *, masquer=True, exclure_instructions=True):
    retenus, constats, exclus = {}, [], []
    for nom, contenu in documents.items():
        examen = examiner_fichier(nom, contenu)
        constats.extend(examen['secrets'] + examen['instructions'])
        if examen['instructions'] and exclure_instructions:
            exclus.append(nom)
            continue
        retenus[nom] = examen['text'] if masquer else contenu
    return {'documents': retenus, 'findings': constats, 'excluded': exclus}


def bloquer_images_externes(texte):
    # Préserver les exemples de code, retirer seulement les chargements d'images rendues.
    morceaux = re.split(r'(```[\s\S]*?```)', texte)
    for i in range(0, len(morceaux), 2):
        morceaux[i] = re.sub(r'!\[[^\]\r\n]*\]\([^\r\n)]*\)', '[Image externe non chargée]', morceaux[i])
        morceaux[i] = re.sub(r'(?i)<img\b[^>]*>', '[Image externe non chargée]', morceaux[i])
        morceaux[i] = re.sub(r'!\[[^\]\r\n]*\]\[[^\]\r\n]*\]', '[Image externe non chargée]', morceaux[i])
        morceaux[i] = morceaux[i].replace('![', '[')
    return ''.join(morceaux)
