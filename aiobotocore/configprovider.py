import logging
import os

from botocore.configprovider import SmartDefaultsConfigStoreFactory

logger = logging.getLogger(__name__)


class AioSmartDefaultsConfigStoreFactory(SmartDefaultsConfigStoreFactory):
    """Async version of SmartDefaultsConfigStoreFactory."""

    async def merge_smart_defaults(
        self, config_store, mode: str, region_name: str | None = None
    ) -> None:
        """Merge smart defaults into the config store asynchronously.

        Args:
            config_store: The configuration store to update.
            mode: The resolution mode ('auto' or specific mode).
            region_name: The AWS region name for region-specific resolution.
        """
        if mode == 'auto':
            mode = await self.resolve_auto_mode(region_name)
        default_configs = (
            self._default_config_resolver.get_default_config_values(mode)
        )
        for config_var in default_configs:
            config_value = default_configs[config_var]
            method = getattr(self, f'_set_{config_var}', None)
            if method:
                method(config_store, config_value)

    async def resolve_auto_mode(
        self, region_name: str | None = None
    ) -> str:
        """Resolve the auto mode based on execution environment and region.

        Args:
            region_name: The AWS region name from the request.

        Returns:
            'in-region' if within the same region, 'cross-region' if different,
            or 'standard' if region cannot be determined.
        """
        current_region: str | None = None
        if os.environ.get('AWS_EXECUTION_ENV'):
            default_region = os.environ.get('AWS_DEFAULT_REGION')
            current_region = os.environ.get('AWS_REGION', default_region)
        if not current_region:
            if self._instance_metadata_region:
                current_region = self._instance_metadata_region
            else:
                try:
                    current_region = await self._imds_region_provider.provide()
                    self._instance_metadata_region = current_region
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "Failed to resolve region from IMDS, "
                        "falling back to 'standard' mode"
                    )

        if current_region:
            if region_name == current_region:
                return 'in-region'
            else:
                return 'cross-region'
        return 'standard'
