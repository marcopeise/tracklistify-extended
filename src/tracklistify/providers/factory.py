"""Provider factory for creating track identification providers."""

# Standard library imports
import os
from typing import Dict

# Local imports
from tracklistify.utils.logger import get_logger

logger = get_logger(__name__)

_provider_factory = None


def create_provider_factory() -> "ProviderFactory":
    """Create and return a provider factory instance."""
    global _provider_factory
    if _provider_factory is None:
        _provider_factory = ProviderFactory()
    return _provider_factory


def clear_provider_cache():
    """Clear the cached providers to force recreation with updated implementations."""
    global _provider_factory
    if _provider_factory is not None:
        _provider_factory.clear_cache()


class ProviderFactory:
    """Factory class to manage identification providers."""

    def __init__(self):
        """Initialize the provider factory."""
        self.providers = {}

    def get_identification_provider(self, provider_name):
        """Retrieve or create an identification provider by name."""
        if provider_name in self.providers:
            return self.providers[provider_name]
        else:
            # Create a new provider instance based on the name
            if provider_name == "shazam":
                from tracklistify.providers.shazam import ShazamProvider

                provider = ShazamProvider()
            elif provider_name == "acrcloud":
                from tracklistify.providers.acrcloud import ACRCloudProvider

                provider = ACRCloudProvider()
            elif provider_name == "acoustid":
                from tracklistify.providers.acoustid import AcoustIDProvider

                provider = AcoustIDProvider()
            else:
                raise ValueError(f"Unknown provider: {provider_name}")
            self.providers[provider_name] = provider
            return provider

    def get_available_providers(self) -> Dict[str, object]:
        """Get all available providers based on configured credentials.

        Returns a dict of provider_name -> provider instance for all
        providers that have valid credentials configured.
        """
        available = {}

        # Shazam is always available (no API key needed)
        try:
            available["shazam"] = self.get_identification_provider("shazam")
        except Exception as e:
            logger.warning(f"Failed to initialize Shazam provider: {e}")

        # ACRCloud requires credentials
        acr_key = os.getenv("TRACKLISTIFY_ACR_ACCESS_KEY", "")
        acr_secret = os.getenv("TRACKLISTIFY_ACR_ACCESS_SECRET", "")
        if acr_key and acr_secret:
            try:
                available["acrcloud"] = self.get_identification_provider(
                    "acrcloud"
                )
                logger.info("ACRCloud provider available for multi-provider mode")
            except Exception as e:
                logger.warning(f"Failed to initialize ACRCloud provider: {e}")

        # AcoustID requires API key and chromaprint
        acoustid_key = os.getenv("TRACKLISTIFY_ACOUSTID_API_KEY", "")
        if acoustid_key:
            try:
                available["acoustid"] = self.get_identification_provider(
                    "acoustid"
                )
                logger.info("AcoustID provider available for multi-provider mode")
            except Exception as e:
                logger.warning(f"Failed to initialize AcoustID provider: {e}")

        return available

    async def close_all(self):
        """Close all providers."""
        for provider in self.providers.values():
            await provider.close()  # Make sure to await the coroutine

    def clear_cache(self):
        """Clear the provider cache to force recreation of providers."""
        self.providers.clear()
