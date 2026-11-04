"""Petit cas pédagogique : gestion d'un stock.

Règles attendues : le produit existe, la quantité retirée est un entier
strictement positif et le stock restant ne devient jamais négatif.
"""

stock = {"stylo": 5, "cahier": 3}


def retirer_produit(nom, quantite):
    if quantite > stock[nom]:
        raise ValueError(
            "La quantité demandée dépasse le stock disponible."
        )

    stock[nom] = stock[nom] - quantite
    return stock[nom]
