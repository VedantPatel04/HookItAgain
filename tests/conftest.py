import os

from cryptography.fernet import Fernet

# this is a throwaway Fernet key for this pytest process. CI has no .env.
# it lives in memory only so it cannot land in git or Actions logs.
os.environ["SECRET_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
