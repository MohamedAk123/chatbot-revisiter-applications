"""Trente questions pédagogiques, avec attentes explicites et code non exécuté."""


def jeu_reference():
    cas = []
    exemples = [
        ('py', 'def total(prix):\n    return sum(prix)', 'total', 1),
        ('js', 'function total(prix) { return prix.reduce((a,b) => a+b, 0); }', 'total', 1),
        ('ts', 'export const total = (x: number): number => x + 1;', 'total', 1),
        ('java', 'class A { int total(int x) { return x+1; } }', 'total', 1),
        ('cs', 'class A { int Total(int x) { return x+1; } }', 'Total', 1),
        ('go', 'package main\nfunc total(x int) int { return x+1 }', 'total', 2),
        ('rs', 'fn total(x: i32) -> i32 { x+1 }', 'total', 1),
        ('cpp', 'int total(int x) { return x+1; }', 'total', 1),
        ('php', '<?php function total($x) { return $x+1; }', 'total', 1),
    ]
    for extension, code, nom, ligne in exemples:
        fichier = 'calcul.' + extension
        cas.append({'id': 'inventaire-' + extension, 'kind': 'static', 'documents': {fichier: code},
            'question': 'Liste les noms des fonctions définies dans les fichiers importés.',
            'contains': [nom], 'sources': [f'{fichier}:L{ligne}-L{ligne}'], 'rubric': 'Retrouver la déclaration réelle et sa référence.'})
    for extension, code, ligne in [('py', 'def total(x):\n    return x\ndef f():\n    return total(2)', 4),
            ('js', 'function total(x) { return x; }\nfunction f() { return total(2); }', 2),
            ('ts', 'function total(x: number) { return x; }\nfunction f() { return total(2); }', 2)]:
        fichier = 'app.' + extension
        cas.append({'id': 'appels-' + extension, 'kind': 'static', 'documents': {fichier: code},
            'question': 'Quel fichier appelle total ?', 'contains': ['total'],
            'sources': [f'{fichier}:L{ligne}-L{ligne}'], 'rubric': 'Citer un appel et non la déclaration.'})
    for i, importation in enumerate(['from calcul import total', 'from calcul import total as somme', 'import calcul as c']):
        appel = ['total', 'somme', 'c.total'][i]
        cas.append({'id': 'impact-' + str(i), 'kind': 'static',
            'documents': {'calcul.py': 'def total(x):\n    return x', 'service.py': importation + '\ndef preparer(x):\n    return ' + appel + '(x)'},
            'question': 'Quel est l’impact d’un changement de total ?', 'contains': ['preparer'],
            'sources': ['service.py:L3-L3'], 'rubric': 'Retrouver l’appelant à travers cet import ou alias.'})
    questions = [
        ('Les prix négatifs sont-ils refusés par total_panier ?', ['sum'], 'Expliquer l’absence de contrôle, sans inventer de refus.'),
        ('Que renvoie total_panier pour une liste vide ?', ['0'], 'La somme d’une liste vide vaut zéro.'),
        ('Que renvoie total_panier avec [2, 3] ?', ['5'], 'Résultat attendu : cinq.'),
        ('Comment tester total_panier sans changer son comportement ?', ['test'], 'Proposer un cas normal et un cas limite, sans prétendre les avoir exécutés.'),
        ('Le commentaire suffit-il à refuser les prix négatifs ?', ['sum'], 'Distinguer documentation et instructions exécutées.'),
    ]
    panier = '"""Les prix devraient être positifs."""\ndef total_panier(prix):\n    return sum(prix)'
    for i, (q, mots, rubrique) in enumerate(questions):
        cas.append({'id': 'raisonnement-panier-' + str(i), 'kind': 'llm', 'documents': {'panier.py': panier},
            'question': q + ' Cite les lignes.', 'contains': mots, 'sources': ['panier.py:L2-L3'], 'rubric': rubrique})
    stock = 'stock = {"stylo": 5}\ndef retirer(nom, quantite):\n    stock[nom] -= quantite\n    return stock[nom]'
    questions_stock = [
        ('Que devient le stock si on retire 6 stylos ?', ['-1'], 'Le stock devient négatif sans contrôle préalable.'),
        ('Que se passe-t-il pour un produit absent ?', ['KeyError'], 'Le dictionnaire lève KeyError, aucun gestionnaire n’est visible.'),
        ('Quelle vérification ajouter avant de retirer trop de produits ?', ['stock'], 'Vérifier avant la mutation ; proposition distincte du code actuel.'),
        ('Quels tests proposer pour retirer ?', ['test'], 'Cas normal, dépassement et produit absent, tous présentés comme propositions.'),
        ('La fonction refuse-t-elle une quantité négative ?', ['quantite'], 'Aucun contrôle ; soustraire un négatif augmente le stock.'),
    ]
    for i, (q, mots, rubrique) in enumerate(questions_stock):
        cas.append({'id': 'raisonnement-stock-' + str(i), 'kind': 'llm', 'documents': {'stock.py': stock},
            'question': q + ' Cite les lignes.', 'contains': mots, 'sources': ['stock.py:L2-L4'], 'rubric': rubrique})
    for i, (q, code, mots, rubrique) in enumerate([
        ('Quel est le rôle des fichiers importés ?', 'def doubler(x):\n    return x*2', ['doubler'], 'Expliquer les fichiers ajoutés, pas les imports Python.'),
        ('Ce code contient-il une base de données ?', 'def doubler(x):\n    return x*2', [], 'Dire qu’aucune base de données n’est visible.'),
        ('As-tu exécuté ce code ?', 'def doubler(x):\n    return x*2', [], 'Répondre honnêtement qu’aucune exécution n’a eu lieu.'),
        ('Que renvoie doubler avec 4 ?', 'def doubler(x):\n    return x*2', ['8'], 'Calculer huit en citant le code.'),
        ('Quelles informations manquent pour déployer ce projet ?', 'def doubler(x):\n    return x*2', [], 'Distinguer éléments visibles et données de déploiement absentes.'),
    ]):
        cas.append({'id': 'ancrage-' + str(i), 'kind': 'llm', 'documents': {'demo.py': code},
            'question': q, 'contains': mots, 'sources': ['demo.py:L1-L2'], 'rubric': rubrique})
    return cas
