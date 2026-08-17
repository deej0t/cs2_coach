"""CS2 Match Share Code decoder and demo downloader.

Share code format: CSGO-xxxxx-xxxxx-xxxxx-xxxxx-xxxxx
Encodes: match_id (uint64), outcome_id (uint64), token (uint16)

The demo URL can be constructed from the match_id via Valve's
Game Coordinator protocol or the Steam Web API.
"""

from __future__ import annotations

import re
import struct
import tempfile
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


def sharecode_to_demo_url(code: str) -> str | None:
    """Try to resolve a share code to a demo download URL.

    Uses the Steam Web API endpoint that resolves match details.
    Returns the demo URL if available, None otherwise.
    """
    info = decode_sharecode(code)
    match_id = info["match_id"]
    outcome_id = info["outcome_id"]
    token = info["token"]

    # Valve's public match info API (no API key needed for CS2)
    # This endpoint returns match details including the demo URL
    api_url = (
        f"https://api.steampowered.com/ICSGOPlayers_730/GetNextMatchSharingCode/v1"
        f"?matchid={match_id}&outcomeid={outcome_id}&token={token}"
    )

    # Alternative: direct GCPD URL pattern (works for recent CS2 matches)
    # Demo URLs follow the pattern: http://replay{cluster}.valve.net/730/{match_id}_{token}.dem.bz2
    # But we don't know the cluster without GC communication.

    return None  # Placeholder — see download_demo for actual logic


def download_demo(code: str, output_dir: str | Path | None = None,
                  steam_api_key: str = "",
                  on_status: callable = None) -> Path | None:
    """Download a CS2 demo using a match share code.

    Tries multiple methods:
    1. Steam Web API match info (if API key provided)
    2. All Valve replay clusters in parallel with GET probes

    The demo URL pattern is:
        http://replay{N}.valve.net/730/{match_id}_{outcome_id}.dem.bz2
    where match_id and outcome_id come from the decoded share code.

    Args:
        code: CS2 match share code
        output_dir: Directory to save the demo. Uses temp dir if None.
        steam_api_key: Optional Steam Web API key for authenticated requests.
        on_status: Optional callback(msg: str) for progress updates.

    Returns:
        Path to the downloaded .dem file, or None if download failed.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _log(msg: str):
        if on_status:
            on_status(msg)

    info = decode_sharecode(code)
    match_id = info["match_id"]
    outcome_id = info["outcome_id"]
    token = info["token"]

    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="cs2demo_"))
    else:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    dem_path = output_dir / f"match_{match_id}.dem"

    # Method 1: Try Steam Web API for demo URL
    if steam_api_key:
        _log("Steam API wird abgefragt...")
        demo_url = _get_demo_url_steam_api(match_id, outcome_id, token,
                                           steam_api_key)
        if demo_url:
            _log(f"Demo-URL via API gefunden, lade herunter...")
            try:
                return _download_and_extract(demo_url, dem_path)
            except Exception:
                _log("API-URL fehlgeschlagen, versuche Replay-Server...")

    # Method 2: Try Valve replay server clusters in parallel
    # URL pattern: http://replay{N}.valve.net/730/{match_id}_{outcome_id}.dem.bz2
    clusters = ["", "1", "2", "3", "4", "5", "6", "7", "8"]
    urls = []
    for c in clusters:
        host = f"replay{c}.valve.net" if c else "replay.valve.net"
        urls.append(f"http://{host}/730/{match_id}_{outcome_id}.dem.bz2")

    _log(f"Pruefe {len(urls)} Valve Replay-Server parallel...")

    def _probe_get(url: str) -> str | None:
        """GET probe — check if server responds 200 then close immediately."""
        try:
            req = urllib.request.Request(url,
                                        headers={"User-Agent": "CS2Coach/1.0"})
            resp = urllib.request.urlopen(req, timeout=8)
            status = resp.status
            resp.close()
            if status == 200:
                return url
        except Exception:
            pass
        return None

    hit_url = None
    with ThreadPoolExecutor(max_workers=len(urls)) as pool:
        futures = {pool.submit(_probe_get, u): u for u in urls}
        for future in as_completed(futures):
            result = future.result()
            if result:
                hit_url = result
                for f in futures:
                    f.cancel()
                break

    if not hit_url:
        _log("Kein Replay-Server hat die Demo. Eventuell abgelaufen oder noch nicht bereit.")
        return None

    _log(f"Server gefunden, lade Demo herunter...")
    return _download_and_extract(hit_url, dem_path)


def _get_demo_url_steam_api(match_id: int, outcome_id: int, token: int,
                            api_key: str) -> str | None:
    """Try to get demo download URL via Steam Web API.

    Uses GetGameMatchHistory to find the match and extract the demo link.
    """
    import json as _json

    # Try the match details endpoint
    url = (
        f"https://api.steampowered.com/ICSGOPlayers_730/GetRecentGames/v1"
        f"?key={api_key}&matchid={match_id}"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "CS2Coach/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = _json.loads(resp.read())
            demo_url = data.get("result", {}).get("demo_url")
            if demo_url:
                return demo_url
    except Exception:
        pass

    return None


def _download_and_extract(url: str, dem_path: Path,
                          timeout: int = 15) -> Path:
    """Download a .dem.bz2 file and extract it.

    timeout is per socket operation — large files still download fine
    as long as data keeps flowing within each timeout window.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "CS2Coach/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")

        compressed = resp.read()

    if url.endswith(".bz2"):
        import bz2
        decompressed = bz2.decompress(compressed)
        dem_path.write_bytes(decompressed)
    else:
        dem_path.write_bytes(compressed)

    return dem_path


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
