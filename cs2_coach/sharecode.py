"""CS2 Match Share Code decoder and demo downloader.

Share code format: CSGO-xxxxx-xxxxx-xxxxx-xxxxx-xxxxx
Encodes: match_id (uint64), outcome_id (uint64), token (uint16)

Demo download strategy:
1. Authenticate to Steam via WebAuth (steam package)
2. Scrape the GCPD (Game Coordinator Player Data) page for download URLs
3. Download .dem.bz2 files from Valve replay servers
4. Decompress and save locally
5. Fall back to local demo detection if Steam login not configured
"""

from __future__ import annotations

import bz2
import json as _json
import os
import pickle
import re
import struct
import time
import urllib.request
from pathlib import Path

# Base-57 alphabet used by Valve's share code encoding
SHARECODE_ALPHABET = "ABCDEFGHJKLMNOPQRSTUVWXYZabcdefhijkmnopqrstuvwxyz23456789"
SHARECODE_PATTERN = re.compile(
    r"^CSGO-([A-Za-z0-9]{5})-([A-Za-z0-9]{5})-([A-Za-z0-9]{5})-([A-Za-z0-9]{5})-([A-Za-z0-9]{5})$"
)


def decode_sharecode(code: str) -> dict:
    """Decode a CS2 match share code into match_id, outcome_id, and token.

    Args:
        code: Share code string like 'CSGO-xxxxx-xxxxx-xxxxx-xxxxx-xxxxx'

    Returns:
        dict with keys: match_id (int), outcome_id (int), token (int)

    Raises:
        ValueError: If the share code format is invalid.
    """
    code = code.strip()
    m = SHARECODE_PATTERN.match(code)
    if not m:
        raise ValueError(f"Ungueltiges Share-Code Format: {code}")

    # Join the 5 groups into one string
    combined = "".join(m.groups())

    # Decode: each character is a digit in base-57.
    # Process characters and accumulate into a byte array via carry arithmetic.
    base = len(SHARECODE_ALPHABET)
    bytes_arr = bytearray(18)

    for ch in reversed(combined):
        idx = SHARECODE_ALPHABET.find(ch)
        if idx < 0:
            raise ValueError(f"Ungueltiges Zeichen im Share-Code: {ch}")
        carry = idx
        for j in range(len(bytes_arr) - 1, -1, -1):
            carry += bytes_arr[j] * base
            bytes_arr[j] = carry & 0xFF
            carry >>= 8

    # Layout (big-endian): token (2) | outcome_id (8) | match_id (8)
    token = struct.unpack_from(">H", bytes_arr, 0)[0]
    outcome_id = struct.unpack_from(">Q", bytes_arr, 2)[0]
    match_id = struct.unpack_from(">Q", bytes_arr, 10)[0]

    return {
        "match_id": match_id,
        "outcome_id": outcome_id,
        "token": token,
    }


def find_cs2_replays_folders() -> list[Path]:
    """Auto-detect CS2 replays folders on the system.

    Checks common Steam installation paths on Windows.
    Returns list of existing replays directories.
    """
    candidates = []

    # Standard Steam paths on Windows
    steam_roots = [
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Steam",
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Steam",
        Path("D:/Steam"),
        Path("D:/SteamLibrary"),
        Path("E:/Steam"),
        Path("E:/SteamLibrary"),
    ]

    # Check Steam's libraryfolders.vdf for additional library paths
    for steam_root in steam_roots[:2]:  # Only check default locations for vdf
        vdf_path = steam_root / "steamapps" / "libraryfolders.vdf"
        if vdf_path.exists():
            try:
                text = vdf_path.read_text(encoding="utf-8", errors="ignore")
                # Simple VDF parser: look for "path" values
                for line in text.splitlines():
                    line = line.strip()
                    if '"path"' in line:
                        parts = line.split('"')
                        if len(parts) >= 4:
                            lib_path = Path(parts[3].replace("\\\\", "/"))
                            if lib_path not in steam_roots:
                                steam_roots.append(lib_path)
            except OSError:
                pass

    cs2_subpath = Path("steamapps/common/Counter-Strike Global Offensive/game/csgo/replays")
    for root in steam_roots:
        replays_dir = root / cs2_subpath
        if replays_dir.is_dir():
            candidates.append(replays_dir)

    return candidates


def find_demo_by_match_id(match_id: int, search_dirs: list[Path | str]) -> Path | None:
    """Find a local demo file by match_id.

    CS2 saves demos as match730_{match_id}_{reservation_id}_{map_id}.dem
    in the replays folder.

    Args:
        match_id: The match_id decoded from a share code.
        search_dirs: List of directories to search for .dem files.

    Returns:
        Path to the matching demo, or None.
    """
    # CS2 zero-pads match_id to 21 digits in filenames
    mid_padded = str(match_id).zfill(21)
    for d in search_dirs:
        d = Path(d)
        if not d.is_dir():
            continue
        # CS2 demo filename pattern: match730_{match_id_padded}_{...}.dem
        for f in d.glob(f"match730_{mid_padded}_*.dem"):
            if f.stat().st_size > 1_000_000:  # Skip tiny/corrupt files
                return f
        # Also try unpadded (in case of manual renames)
        if mid_padded != str(match_id):
            for f in d.glob(f"match730_{match_id}_*.dem"):
                if f.stat().st_size > 1_000_000:
                    return f
    return None


def find_demos_for_codes(
    codes: list[str],
    demo_folder: str = "",
    on_status: callable = None,
) -> dict[str, Path | None]:
    """Find local demo files for a list of share codes.

    Searches the configured demo_folder and auto-detected CS2 replays folders.

    Args:
        codes: List of share code strings.
        demo_folder: Configured demo folder path (from settings).
        on_status: Optional callback(msg: str) for progress updates.

    Returns:
        Dict mapping share code -> Path (or None if not found).
    """
    def _log(msg: str):
        if on_status:
            on_status(msg)

    # Build list of directories to search
    search_dirs: list[Path] = []
    if demo_folder and Path(demo_folder).is_dir():
        search_dirs.append(Path(demo_folder))
    # Auto-detect CS2 replays folders
    for rdir in find_cs2_replays_folders():
        if rdir not in search_dirs:
            search_dirs.append(rdir)

    if search_dirs:
        _log(f"Suche Demos in {len(search_dirs)} Ordner(n): {', '.join(p.name for p in search_dirs)}")
    else:
        _log("Kein Demo-Ordner konfiguriert und CS2-Replays-Ordner nicht gefunden.")

    results: dict[str, Path | None] = {}
    for code in codes:
        try:
            info = decode_sharecode(code)
            match_id = info["match_id"]
            found = find_demo_by_match_id(match_id, search_dirs)
            results[code] = found
        except ValueError:
            results[code] = None

    return results


def validate_sharecode(code: str) -> bool:
    """Check if a string is a valid CS2 share code format."""
    return bool(SHARECODE_PATTERN.match(code.strip()))


def get_next_sharecode(
    steam_api_key: str,
    steam_id: str,
    auth_token: str,
    known_code: str,
) -> str | None:
    """Fetch the next match share code after known_code via Steam Web API.

    Uses ICSGOPlayers_730/GetNextMatchSharingCode/v1.

    Args:
        steam_api_key: Steam Web API key
        steam_id: Player's SteamID64
        auth_token: CS2 auth token from game settings
        known_code: Last known share code (CSGO-xxxxx-...)

    Returns:
        Next share code string, or None if no new matches.

    Raises:
        RuntimeError: If API returns an error.
    """
    import json as _json

    url = (
        f"https://api.steampowered.com/ICSGOPlayers_730/GetNextMatchSharingCode/v1"
        f"?key={steam_api_key}"
        f"&steamid={steam_id}"
        f"&steamidkey={auth_token}"
        f"&knowncode={known_code}"
    )

    req = urllib.request.Request(url, headers={"User-Agent": "CS2Coach/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = _json.loads(resp.read())

    result = data.get("result", {})

    if result.get("nextcode") == "n/a":
        return None  # No new matches

    next_code = result.get("nextcode", "")
    if next_code and validate_sharecode(next_code):
        return next_code

    return None


def fetch_all_new_codes(
    steam_api_key: str,
    steam_id: str,
    auth_token: str,
    last_known_code: str,
    max_codes: int = 20,
) -> list[str]:
    """Fetch all new share codes since last_known_code.

    Chains GetNextMatchSharingCode calls until no more new codes.

    Returns:
        List of new share codes (oldest first).
    """
    codes = []
    current = last_known_code

    for _ in range(max_codes):
        try:
            next_code = get_next_sharecode(
                steam_api_key, steam_id, auth_token, current,
            )
        except Exception:
            break

        if not next_code:
            break

        codes.append(next_code)
        current = next_code
        time.sleep(0.3)  # Rate limit

    return codes


# ---------------------------------------------------------------------------
# Steam Web Login & GCPD Demo Download
# ---------------------------------------------------------------------------

_SESSION_FILE = Path(__file__).resolve().parent.parent / ".steam_session"

# Regex to extract demo download URLs — broad pattern catches URLs anywhere in text
_GCPD_DEMO_URL_RE = re.compile(
    r'(https?://replay\d+\.valve\.net/730/(\d+)_\d+\.dem\.bz2)'
)


class SteamLoginError(Exception):
    """Base exception for Steam login failures."""

class Steam2FARequired(SteamLoginError):
    """Raised when Steam Guard 2FA code is required."""

class SteamEmailCodeRequired(SteamLoginError):
    """Raised when Steam Guard email code is required."""


_STEAM_API = "https://api.steampowered.com"
_STEAM_COMMUNITY = "https://steamcommunity.com"
_STEAM_STORE = "https://store.steampowered.com"
_STEAM_HELP = "https://help.steampowered.com"


def steam_login(
    username: str,
    password: str,
    twofactor_code: str = "",
    email_code: str = "",
) -> "requests.Session":
    """Authenticate to Steam via IAuthenticationService API.

    Uses the new Steam auth flow (2023+):
    1. Get RSA public key for username
    2. Encrypt password with RSA
    3. BeginAuthSessionViaCredentials
    4. Submit 2FA/email code if needed
    5. PollAuthSessionStatus for tokens
    6. Finalize login with session cookies

    Returns an authenticated requests.Session for Steam web access.
    """
    import requests as _requests
    from base64 import b64encode

    session = _requests.Session()
    session.headers["User-Agent"] = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    # Step 1: Get RSA public key
    rsa_resp = session.get(
        f"{_STEAM_API}/IAuthenticationService/GetPasswordRSAPublicKey/v1",
        params={"account_name": username},
        timeout=15,
    ).json()

    rsa_data = rsa_resp.get("response", {})
    if not rsa_data.get("publickey_mod"):
        raise SteamLoginError("Konnte RSA-Key nicht laden. Benutzername korrekt?")

    # Step 2: Encrypt password with RSA
    mod = int(rsa_data["publickey_mod"], 16)
    exp = int(rsa_data["publickey_exp"], 16)
    timestamp = rsa_data["timestamp"]

    try:
        from Cryptodome.PublicKey import RSA as _RSA
        from Cryptodome.Cipher import PKCS1_v1_5 as _PKCS
        rsa_key = _RSA.construct((mod, exp))
        cipher = _PKCS.new(rsa_key)
        encrypted = cipher.encrypt(password.encode("utf-8"))
    except ImportError:
        # Fallback: use rsa package
        import rsa as _rsa
        pub_key = _rsa.PublicKey(mod, exp)
        encrypted = _rsa.encrypt(password.encode("utf-8"), pub_key)

    encrypted_b64 = b64encode(encrypted).decode("ascii")

    # Step 3: BeginAuthSessionViaCredentials
    begin_resp = session.post(
        f"{_STEAM_API}/IAuthenticationService/BeginAuthSessionViaCredentials/v1",
        data={
            "account_name": username,
            "encrypted_password": encrypted_b64,
            "encryption_timestamp": timestamp,
            "persistence": "1",
            "website_id": "Community",
        },
        headers={
            "Referer": f"{_STEAM_COMMUNITY}/",
            "Origin": _STEAM_COMMUNITY,
        },
        timeout=15,
    ).json()

    begin_data = begin_resp.get("response", {})
    client_id = begin_data.get("client_id")
    request_id = begin_data.get("request_id")
    steamid = begin_data.get("steamid")

    if not client_id:
        msg = begin_data.get("extended_error_message", "").strip()
        if not msg:
            msg = "Benutzername oder Passwort falsch."
        raise SteamLoginError(msg)

    # Step 4: Check if 2FA or email guard is needed
    confirmations = begin_data.get("allowed_confirmations", [])
    needs_2fa = any(c.get("confirmation_type") == 3 for c in confirmations)
    needs_email = any(c.get("confirmation_type") == 2 for c in confirmations)

    if needs_2fa or needs_email:
        code = twofactor_code if needs_2fa else email_code
        code_type = 3 if needs_2fa else 2

        if not code:
            if needs_2fa:
                raise Steam2FARequired("Steam Guard 2FA-Code erforderlich.")
            else:
                raise SteamEmailCodeRequired("Steam Guard E-Mail-Code erforderlich.")

        guard_resp = session.post(
            f"{_STEAM_API}/IAuthenticationService/UpdateAuthSessionWithSteamGuardCode/v1",
            data={
                "client_id": client_id,
                "steamid": steamid,
                "code": code,
                "code_type": str(code_type),
            },
            timeout=15,
        ).json()

        # Check for errors (wrong code)
        guard_data = guard_resp.get("response", {})
        if guard_resp.get("response") is None and "error" in str(guard_resp).lower():
            raise SteamLoginError("Ungueltiger Steam Guard Code.")

    # Step 5: Poll for auth session status
    poll_resp = session.post(
        f"{_STEAM_API}/IAuthenticationService/PollAuthSessionStatus/v1",
        data={
            "client_id": client_id,
            "request_id": request_id,
        },
        timeout=15,
    ).json()

    poll_data = poll_resp.get("response", {})
    refresh_token = poll_data.get("refresh_token")
    access_token = poll_data.get("access_token")

    if not refresh_token:
        raise SteamLoginError("Login fehlgeschlagen — kein Token erhalten. Code korrekt?")

    # Step 6: Finalize login — set session cookies
    _finalize_steam_login(session, steamid, refresh_token, access_token)

    # Save session for reuse
    _save_session(session)
    return session


def _finalize_steam_login(
    session: "requests.Session",
    steamid: str,
    refresh_token: str,
    access_token: str,
) -> None:
    """Set Steam session cookies using the obtained tokens."""
    import requests as _requests
    import hashlib

    # Generate a session ID
    import secrets
    session_id = secrets.token_hex(12)

    # Set the login secure cookie (JWT token)
    token_value = f"{steamid}%7C%7C{access_token}"

    for domain in ["steamcommunity.com", "store.steampowered.com", "help.steampowered.com"]:
        session.cookies.set("sessionid", session_id, domain=domain)
        session.cookies.set("steamLoginSecure", token_value, domain=domain, secure=True)
        session.cookies.set("Steam_Language", "english", domain=domain)
        session.cookies.set("birthtime", "-3333", domain=domain)

    # Also do the finalizelogin call which sets additional cookies
    try:
        finalize_resp = session.post(
            "https://login.steampowered.com/jwt/finalizelogin",
            data={
                "nonce": refresh_token,
                "sessionid": session_id,
                "redir": f"{_STEAM_COMMUNITY}/login/home/?goto=",
            },
            timeout=15,
        )
        if finalize_resp.status_code == 200:
            fin_data = finalize_resp.json()
            for transfer in fin_data.get("transfer_info", []):
                url = transfer.get("url", "")
                params = transfer.get("params", {})
                if url and params:
                    try:
                        session.post(url, data=params, timeout=10)
                    except Exception:
                        pass
    except Exception:
        pass  # Non-critical — cookies from step above should suffice


def _save_session(session: "requests.Session") -> None:
    """Persist session cookies to disk."""
    try:
        with open(_SESSION_FILE, "wb") as f:
            pickle.dump(session.cookies, f)
    except OSError:
        pass


def load_steam_session() -> "requests.Session | None":
    """Load a previously saved Steam session. Returns None if unavailable or expired."""
    try:
        import requests as _requests
    except ImportError:
        return None

    if not _SESSION_FILE.exists():
        return None

    try:
        with open(_SESSION_FILE, "rb") as f:
            cookies = pickle.load(f)
    except (OSError, pickle.UnpicklingError, EOFError):
        return None

    session = _requests.Session()
    session.cookies = cookies
    session.headers["User-Agent"] = "Mozilla/5.0 CS2Coach/1.0"

    # Quick validation: check if session is still alive
    try:
        resp = session.get(
            "https://steamcommunity.com/my",
            allow_redirects=False,
            timeout=10,
        )
        # If redirected to login page, session is expired
        if resp.status_code == 302:
            location = resp.headers.get("Location", "")
            if "login" in location.lower():
                clear_steam_session()
                return None
        return session
    except Exception:
        return None


def clear_steam_session() -> None:
    """Remove stored Steam session."""
    try:
        _SESSION_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def is_steam_logged_in() -> bool:
    """Check if a valid Steam session exists."""
    session = load_steam_session()
    return session is not None


def fetch_gcpd_demo_urls(
    session: "requests.Session",
    steam_id: str = "",
    tabs: list[str] | None = None,
    on_status: callable = None,
) -> list[dict]:
    """Scrape the GCPD page for demo download URLs.

    The GCPD page loads match data via AJAX (not in the initial HTML).
    Flow: load page → extract continue_token & sessionid → AJAX calls to fetch data.

    Args:
        session: Authenticated requests.Session from steam_login().
        steam_id: SteamID64 (used to construct GCPD URL).
        tabs: Which match history tabs to scrape.
               Default: ['matchhistorypremier', 'matchhistorycompetitive', 'matchhistorywingman']
        on_status: Optional status callback.

    Returns:
        List of dicts: {url, match_id, tab}
    """
    if tabs is None:
        tabs = ["matchhistorypremier", "matchhistorycompetitive", "matchhistorywingman"]

    def _log(msg: str):
        if on_status:
            on_status(msg)

    base_url = "https://steamcommunity.com"
    if steam_id:
        profile_url = f"{base_url}/profiles/{steam_id}/gcpd/730"
    else:
        profile_url = f"{base_url}/my/gcpd/730"

    all_demos = []
    seen_urls = set()

    for tab in tabs:
        _log(f"Lade Match-History: {tab}...")
        url = f"{profile_url}?tab={tab}"

        try:
            resp = session.get(url, timeout=20, allow_redirects=True)
            if resp.status_code != 200:
                _log(f"Fehler beim Laden von {tab}: HTTP {resp.status_code}")
                continue

            html = resp.text

            # Check if we got redirected to login
            if "/login" in resp.url.lower():
                _log("Steam-Session abgelaufen. Bitte erneut einloggen.")
                return []

            # Use final URL after redirects (e.g. /profiles/... → /id/...)
            gcpd_base = resp.url.split("?")[0]

            # Scan initial page for demo URLs (usually empty for CS2)
            _extract_demo_urls(html, tab, all_demos, seen_urls)

            # Extract tokens for AJAX data loading (quotes can be single or double)
            cont_match = re.search(r"g_sGcContinueToken\s*=\s*['\"]([^'\"]*)['\"]", html)
            sessid_match = re.search(r"g_sessionID\s*=\s*['\"]([^'\"]*)['\"]", html)

            # Fallback: get sessionid from cookies if not in page JS
            sessid = None
            if sessid_match:
                sessid = sessid_match.group(1)
            else:
                sessid = session.cookies.get("sessionid", domain="steamcommunity.com")

            if cont_match and sessid and cont_match.group(1):
                cont_token = cont_match.group(1)
                _log(f"GCPD Token gefunden, lade Daten via AJAX...")

                # AJAX calls to load match data (initial + pagination, up to 5 pages)
                for page in range(5):
                    ajax_url = (
                        f"{gcpd_base}"
                        f"?tab={tab}"
                        f"&continue_token={cont_token}"
                        f"&sessionid={sessid}"
                        f"&ajax=1"
                    )

                    try:
                        aresp = session.get(
                            ajax_url,
                            timeout=20,
                            headers={
                                "X-Requested-With": "XMLHttpRequest",
                                "Referer": f"{gcpd_base}?tab={tab}",
                                "Accept": "text/html, */*; q=0.01",
                            },
                        )
                        if aresp.status_code != 200:
                            _log(f"AJAX Seite {page + 1}: HTTP {aresp.status_code}")
                            break

                        content = aresp.text

                        # Steam AJAX can return JSON with HTML embedded
                        try:
                            json_data = aresp.json()
                            if isinstance(json_data, dict):
                                html_part = json_data.get("html", json_data.get("results_html", ""))
                                if html_part:
                                    content = html_part
                                # Extract URLs from HTML content
                                page_count = _extract_demo_urls(content, tab, all_demos, seen_urls)

                                # Update continue_token from JSON response
                                new_token = json_data.get("continue_token", "")
                                if new_token and str(new_token) != cont_token:
                                    cont_token = str(new_token)
                                else:
                                    break  # No new token — done

                                if page_count == 0:
                                    break  # No new URLs — done

                                time.sleep(0.5)
                                continue
                        except (ValueError, KeyError):
                            pass

                        # Plain HTML response — extract URLs and next token
                        page_count = _extract_demo_urls(content, tab, all_demos, seen_urls)

                        # Look for updated continue_token in HTML
                        cont_match2 = re.search(r"g_sGcContinueToken\s*=\s*['\"]([^'\"]*)['\"]", content)
                        # Also check for token in JSON-like structure in response
                        cont_match3 = re.search(r'"continue_token"\s*:\s*"?(\d+)"?', content)

                        new_token = None
                        if cont_match2 and cont_match2.group(1):
                            new_token = cont_match2.group(1)
                        elif cont_match3:
                            new_token = cont_match3.group(1)

                        if new_token and new_token != cont_token:
                            cont_token = new_token
                        elif page_count == 0:
                            # No new URLs and no new token — done with this tab
                            break

                        time.sleep(0.5)
                    except Exception as e:
                        _log(f"AJAX Fehler Seite {page + 1}: {e}")
                        break
            else:
                _log(f"Kein Continue-Token gefunden fuer {tab}")

            tab_count = len([d for d in all_demos if d["tab"] == tab])
            _log(f"{tab}: {tab_count} Demos gefunden")

        except Exception as e:
            _log(f"Fehler bei {tab}: {e}")
            continue

        time.sleep(0.3)

    _log(f"Gesamt: {len(all_demos)} Demo-Downloads verfuegbar")
    return all_demos


def _extract_demo_urls(text: str, tab: str, all_demos: list, seen_urls: set) -> int:
    """Extract demo download URLs and match dates from HTML content.

    Each match block contains a date (YYYY-MM-DD HH:MM:SS GMT) followed by
    a download URL. We pair them by position in the HTML.

    Returns the number of new URLs found.
    """
    # Collect dates and URLs with their positions
    dates = [(m.start(), m.group(1)) for m in re.finditer(
        r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} GMT)", text)]
    urls = [(m.start(), m.group(1), m.group(2)) for m in _GCPD_DEMO_URL_RE.finditer(text)]

    # Pair each URL with the closest preceding date
    count = 0
    for url_pos, demo_url, match_id_str in urls:
        if demo_url in seen_urls:
            continue

        # Find the nearest date that appears before this URL
        match_date = ""
        for date_pos, date_str in reversed(dates):
            if date_pos < url_pos:
                match_date = date_str
                break

        seen_urls.add(demo_url)
        all_demos.append({
            "url": demo_url,
            "match_id": int(match_id_str),
            "tab": tab,
            "date": match_date,
        })
        count += 1
    return count


def _encode_varint(value: int) -> bytes:
    """Encode an integer as a protobuf varint."""
    result = bytearray()
    while value > 0x7F:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value & 0x7F)
    return bytes(result)


def _write_demo_info(dem_path: Path, date_str: str) -> None:
    """Create a minimal .dem.info sidecar with the match timestamp.

    The .dem.info protobuf format:
      field 1 (varint): reservation/match ID
      field 2 (varint): Unix timestamp
    """
    from datetime import datetime, timezone

    try:
        # Parse "YYYY-MM-DD HH:MM:SS GMT" → Unix timestamp
        dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S GMT").replace(tzinfo=timezone.utc)
        ts = int(dt.timestamp())

        # Extract match ID from dem filename: match730_{matchid}_{...}.dem
        name = dem_path.stem
        parts = name.replace("match730_", "").split("_")
        match_id = int(parts[0]) if parts else 0

        # Build minimal protobuf: field 1 = match_id, field 2 = timestamp
        data = bytearray()
        data.append(0x08)  # field 1, wire type 0 (varint)
        data.extend(_encode_varint(match_id))
        data.append(0x10)  # field 2, wire type 0 (varint)
        data.extend(_encode_varint(ts))

        info_path = Path(str(dem_path) + ".info")
        info_path.write_bytes(bytes(data))
    except Exception:
        pass  # Non-critical — parser falls back to file mtime


def download_demo_bz2(
    url: str,
    dest_folder: str | Path,
    match_date: str = "",
    on_status: callable = None,
) -> Path | None:
    """Download a .dem.bz2 file from Valve replay servers and decompress it.

    Args:
        url: Direct download URL (https://replay{N}.valve.net/730/{id}_{id}.dem.bz2)
        dest_folder: Directory to save the decompressed .dem file.
        match_date: Match date string like "2026-08-23 19:56:25 GMT" (for .dem.info).
        on_status: Optional status callback.

    Returns:
        Path to the decompressed .dem file, or None on failure.
    """
    dest_folder = Path(dest_folder)
    dest_folder.mkdir(parents=True, exist_ok=True)

    def _log(msg: str):
        if on_status:
            on_status(msg)

    # Extract filename from URL
    bz2_name = url.rsplit("/", 1)[-1]  # e.g. "12345_67890.dem.bz2"
    dem_name = bz2_name.replace(".bz2", "")  # e.g. "12345_67890.dem"

    # Prepend "match730_" to match CS2's local naming convention
    if not dem_name.startswith("match730_"):
        dem_name = f"match730_{dem_name}"

    dest_path = dest_folder / dem_name

    # Skip if already downloaded
    if dest_path.exists() and dest_path.stat().st_size > 1_000_000:
        _log(f"Bereits vorhanden: {dem_name}")
        return dest_path

    _log(f"Lade herunter: {bz2_name}...")
    bz2_path = dest_folder / bz2_name

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "CS2Coach/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()

        # Save compressed file temporarily
        bz2_path.write_bytes(data)
        _log(f"Heruntergeladen: {len(data) / 1024 / 1024:.1f} MB (komprimiert)")

        # Decompress
        _log("Entpacke...")
        decompressed = bz2.decompress(data)
        dest_path.write_bytes(decompressed)
        _log(f"Entpackt: {len(decompressed) / 1024 / 1024:.1f} MB → {dem_name}")

        # Remove bz2 file
        try:
            bz2_path.unlink()
        except OSError:
            pass

        # Create .dem.info sidecar with match timestamp
        if match_date:
            _write_demo_info(dest_path, match_date)

        return dest_path

    except Exception as e:
        _log(f"Download-Fehler: {e}")
        # Clean up partial files
        for p in [bz2_path, dest_path]:
            try:
                if p.exists() and p.stat().st_size < 1_000_000:
                    p.unlink()
            except OSError:
                pass
        return None
