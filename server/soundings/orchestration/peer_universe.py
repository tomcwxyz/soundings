"""Say plainly what a set of comparison places is.

Peer comparisons in Soundings are against every place at the same geography
level that has a value. That is a fair national context, but it is not a
set of similar places, and the 9 November 2026 baseline found answers
calling it one. Every comparison now carries this description.
"""

_LEVEL_LABELS = {
    "ltla24": "local authority districts",
    "utla24": "upper-tier and unitary authorities",
    "lsoa21": "neighbourhoods (LSOAs)",
    "msoa21": "MSOAs",
    "wd24": "wards",
    "dz22": "Scottish Data Zones",
    "pcon24": "parliamentary constituencies",
    "rgn": "regions",
    "ctry": "countries",
}


def level_label(place_type: str) -> str:
    return _LEVEL_LABELS.get(place_type, f"'{place_type}' areas")


def describe_peer_universe(place_type: str, n_with_values: int) -> str:
    return (
        f"All {n_with_values} {level_label(place_type)} with a value for this indicator. "
        "This is every place at this level, not a group of similar places: do not call "
        "them 'similar' or 'comparable' places."
    )
