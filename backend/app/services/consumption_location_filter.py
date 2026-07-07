from flask import current_app, has_app_context

from app.models import ConsumptionRecord
from app.services.import_service import normalize_allowed_transaction_locations
from app.services.runtime_config import get_effective_config


ENABLED_TRANSACTION_LOCATION_IDS_KEY = "CONSUMPTION_ENABLED_TRANSACTION_LOCATION_IDS"


def get_enabled_transaction_location_ids(config: dict | None = None) -> list[str]:
    if config is None:
        if not has_app_context():
            return []
        config = get_effective_config(current_app.config)
    return normalize_allowed_transaction_locations(
        config.get(ENABLED_TRANSACTION_LOCATION_IDS_KEY, [])
    )


def apply_enabled_transaction_location_filter(query, config: dict | None = None):
    location_ids = get_enabled_transaction_location_ids(config)
    if not location_ids:
        return query
    return query.filter(ConsumptionRecord.channel_id.in_(location_ids))
