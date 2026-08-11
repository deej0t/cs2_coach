"""CS2 map radar metadata for 2D position visualization."""

# Radar coordinate transform: px = (game_x - pos_x) / scale
#                              py = (pos_y - game_y) / scale
# Values from CS2 radar .txt files (pos_x, pos_y, scale)

MAP_RADAR_DATA: dict[str, dict] = {
    "mirage": {"pos_x": -3230, "pos_y": 1713, "scale": 5.0},
    "inferno": {"pos_x": -2087, "pos_y": 3870, "scale": 4.9},
    "dust2": {"pos_x": -2476, "pos_y": 3239, "scale": 4.4},
    "nuke": {"pos_x": -3453, "pos_y": 2887, "scale": 7.0},
    "ancient": {"pos_x": -2953, "pos_y": 2164, "scale": 5.0},
    "anubis": {"pos_x": -2796, "pos_y": 3328, "scale": 5.22},
    "vertigo": {"pos_x": -3168, "pos_y": 1762, "scale": 4.0},
    "overpass": {"pos_x": -4831, "pos_y": 1781, "scale": 5.2},
    "train": {"pos_x": -2477, "pos_y": 2392, "scale": 4.7},
    "cache": {"pos_x": -2000, "pos_y": 3250, "scale": 5.5},
}

RADAR_IMAGE_SIZE = 1024  # standard radar image resolution


def game_to_radar(game_x: float, game_y: float, map_name: str) -> tuple[float, float] | None:
    """Convert CS2 game coordinates to radar pixel coordinates (0-1024).

    Returns None if map is not supported.
    """
    key = map_name.lower()
    if key not in MAP_RADAR_DATA:
        return None

    data = MAP_RADAR_DATA[key]
    px = (game_x - data["pos_x"]) / data["scale"]
    py = (data["pos_y"] - game_y) / data["scale"]
    return (px, py)
