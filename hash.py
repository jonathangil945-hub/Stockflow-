"""Genera el hash de una contraseña para guardarla en la tabla users.

Uso:  python hash.py MiClaveSegura
(Normalmente no hace falta: el panel Usuarios ya guarda las claves con hash.)
"""
import getpass
import sys

from werkzeug.security import generate_password_hash

password = sys.argv[1] if len(sys.argv) > 1 else getpass.getpass("Contraseña: ")
print(generate_password_hash(password))
