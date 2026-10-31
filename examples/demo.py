"""Exemple autorisé et sans secret : calculer le total d'un panier."""


def total_panier(prix: list[float]) -> float:
    """Retourne la somme des prix. Les valeurs négatives restent autorisées ici."""
    return sum(prix)
