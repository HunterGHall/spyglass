import configparser
import os

from instagrapi import Client

DEFAULT_CONFIG_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "credentials.conf"
)


def login(username: str, password: str, session_file: str | None = None) -> Client:
    """Log in to Instagram and return an authenticated instagrapi Client.

    If `session_file` is given and already exists, its saved session is
    loaded first so `login()` can reuse cookies/device info instead of doing
    a full fresh login every time. Either way, the resulting session is
    written back to `session_file` (if given) so later runs can reuse it.
    """
    client = Client()

    if session_file and os.path.exists(session_file):
        client.load_settings(session_file)

    client.login(username, password)

    if session_file:
        client.dump_settings(session_file)

    return client


def login_from_env(session_file: str | None = None) -> Client:
    """Convenience wrapper: read INSTAGRAM_USERNAME / INSTAGRAM_PASSWORD from
    the environment and log in with them."""
    username = os.environ["INSTAGRAM_USERNAME"]
    password = os.environ["INSTAGRAM_PASSWORD"]
    return login(username, password, session_file=session_file)


def login_from_config(
    config_file: str = DEFAULT_CONFIG_FILE,
    section: str = "instagram",
    session_file: str | None = None,
) -> Client:
    """Read username/password out of a credentials.conf-style INI file and
    log in with them.

    Expected format:

        [instagram]
        username = your_username
        password = your_password
    """
    if not os.path.exists(config_file):
        raise FileNotFoundError(
            f"Credentials file not found: {config_file}. Create it with a "
            f"[{section}] section containing 'username' and 'password'."
        )

    parser = configparser.ConfigParser()
    parser.read(config_file)

    if not parser.has_section(section):
        raise ValueError(f"'{config_file}' has no [{section}] section.")

    username = parser.get(section, "username", fallback=None)
    password = parser.get(section, "password", fallback=None)
    if not username or not password:
        raise ValueError(
            f"'{config_file}' is missing 'username' or 'password' under [{section}]."
        )

    return login(username, password, session_file=session_file)
