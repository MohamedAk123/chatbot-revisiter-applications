from exemple_stock import retirer_produit, stock

# Cas normal
stock["stylo"] = 5

resultat = retirer_produit("stylo", 2)

assert resultat == 3
assert stock["stylo"] == 3
print("OK : le retrait normal fonctionne.")

# Retrait supérieur au stock disponible
stock["stylo"] = 5

try:
    retirer_produit("stylo", 8)
except ValueError:
    assert stock["stylo"] == 5
    print("OK : le retrait excessif est refusé, le stock reste intact.")
else:
    raise AssertionError("Le retrait excessif aurait dû être refusé.")