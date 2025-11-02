# scripts/generate_vapid_keys.py
import base64, os
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

os.makedirs('secrets', exist_ok=True)

private_key = ec.generate_private_key(ec.SECP256R1())
priv_pem = private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption()
)
with open('secrets/vapid_private.pem', 'wb') as f:
    f.write(priv_pem)

pub_bytes = private_key.public_key().public_bytes(
    encoding=serialization.Encoding.X962,
    format=serialization.PublicFormat.UncompressedPoint
)
pub_b64 = base64.urlsafe_b64encode(pub_bytes).decode().rstrip('=')
with open('secrets/vapid_public_b64.txt', 'w') as f:
    f.write(pub_b64)

print('✔ Clave privada: secrets/vapid_private.pem')
print('✔ Public key (base64 url-safe):', pub_b64)
print('\nAñade a .env:')
print('VAPID_PRIVATE_KEY_PEM_PATH=secrets/vapid_private.pem')
print(f'VAPID_PUBLIC_KEY_B64={pub_b64}')
print('VAPID_SUBJECT=mailto:tucorreo@ejemplo.com')
