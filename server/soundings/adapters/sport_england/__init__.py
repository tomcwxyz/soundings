"""Sport England Active Lives adapter.

The loader doubles as the read adapter (loader-mode `fetch_indicator`
comes from `LoaderAdapter`), matching the pattern used by FoE green
space, IMD, and other bulk-download sources.
"""

from soundings.adapters.sport_england.loader import SportEnglandActiveLivesLoader

SportEnglandActiveLivesAdapter = SportEnglandActiveLivesLoader

__all__ = ["SportEnglandActiveLivesAdapter", "SportEnglandActiveLivesLoader"]
