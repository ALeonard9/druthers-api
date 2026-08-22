'''
Privacy contract for product analytics events.

Every event written to the product_events table goes through ProductEvent,
which rejects the payload rather than sanitising it. The forbidden-key check
is substring-based on purpose: 'display_name' and 'search_term' must fail as
surely as 'name' and 'search' do. See docs/PRODUCT-METRICS.md.
'''

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict

# Privacy constraint: keys or substrings that trigger a payload rejection
FORBIDDEN_PAYLOAD_KEYS = {
    'email',
    'handle',
    'search',
    'note',
    'title',
    'name',
    'password',
}


@dataclass
class ProductEvent:
    '''One analytics event, validated at construction.'''

    user_id: uuid.UUID
    event_type: str
    payload: Dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self):
        self._validate_privacy()
        self._validate_event_type()

    def _validate_privacy(self):
        '''Ensure no PII or raw text fields exist in the payload.'''
        for key in self.payload:
            normalized_key = key.lower()
            for forbidden in FORBIDDEN_PAYLOAD_KEYS:
                if forbidden in normalized_key:
                    raise ValueError(
                        f"Privacy violation: payload contains forbidden key '{key}'"
                    )

        # Check string values for obvious email formats just in case
        for val in self.payload.values():
            if isinstance(val, str) and '@' in val and '.' in val:
                raise ValueError(
                    'Privacy violation: payload value looks like an email address'
                )

    def _validate_event_type(self):
        '''Reject anything outside the agreed event vocabulary.'''
        allowed_events = {
            'signup_completed',
            'onboarding_started',
            'first_item_added',
            'fifth_item_ranked',
            'first_share',
            'invite_opened',
            'friendship_established',
            'comparison_viewed',
            'returning_session',
            'profile_completed',
        }
        if self.event_type not in allowed_events:
            raise ValueError(f"Unknown event type: {self.event_type}")

    def to_json(self) -> str:
        '''Serialise for insertion, with UUID and timestamp made JSON-safe.'''
        data = asdict(self)
        data['user_id'] = str(data['user_id'])
        data['occurred_at'] = data['occurred_at'].isoformat()
        return json.dumps(data)
