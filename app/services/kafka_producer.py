"""
app/services/kafka_producer.py

Async Kafka producer using aiokafka.
Publishes events to the devpad_events topic (fire-and-forget).

Fire-and-forget means: we do NOT await delivery confirmation.
The API response is not delayed by Kafka. If Kafka is temporarily
unavailable, the event is lost — acceptable for activity logs,
not acceptable for financial transactions.

All event types and their payloads are defined here.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaError

from app.core.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

_producer: Optional[AIOKafkaProducer] = None


async def get_producer() -> AIOKafkaProducer:
    global _producer
    if _producer is None:
        _producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            # acks=1: leader acknowledges; good balance of speed and durability
            acks=1,
        )
        await _producer.start()
    return _producer


async def stop_producer():
    global _producer
    if _producer is not None:
        await _producer.stop()
        _producer = None


async def publish_event(
    event_type: str,
    user_id: Optional[int] = None,
    guest_id: Optional[str] = None,
    resource_id: Optional[str] = None,
    ip_address: str = "",
    user_agent: str = "",
    metadata: Optional[dict] = None,
    instance_id: Optional[str] = None,
) -> None:
    """
    Publish one event to Kafka. Non-blocking — errors are logged, not raised.
    The API response is never delayed or failed due to Kafka issues.

    event_type examples:
      user_signup, user_login, note_created, note_updated,
      note_deleted, note_restored, note_viewed, note_searched,
      invite_sent, invite_accepted, permission_revoked,
      public_link_enabled, public_link_disabled, guest_view,
      infra_kafka_broker_down, infra_nginx_down, infra_api_crash
    """
    payload = {
        "event_type": event_type,
        "user_id": user_id,
        "guest_id": guest_id,
        "resource_id": resource_id,
        "ip_address": ip_address,
        "user_agent": user_agent,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "instance_id": instance_id or settings.instance_id,
        "metadata": metadata or {},
    }

    try:
        producer = await get_producer()
        # send_and_wait would block until ack — we use send() to fire-and-forget
        await producer.send(settings.kafka_topic, value=payload)
    except KafkaError as e:
        # Log the error but never propagate it — activity logging must never
        # break the main API flow.
        logger.error("Kafka publish failed [%s]: %s", event_type, e)
    except Exception as e:
        logger.error("Unexpected error publishing event [%s]: %s", event_type, e)


# ── Convenience wrappers ────────────────────────────────────────
# These give a clean, readable call site in route handlers and services.

async def event_user_signup(user_id: int, email: str, ip: str, ua: str):
    await publish_event("user_signup", user_id=user_id, ip_address=ip, user_agent=ua,
                        metadata={"email": email})


async def event_user_login(user_id: int, ip: str, ua: str):
    await publish_event("user_login", user_id=user_id, ip_address=ip, user_agent=ua)


async def event_note_created(user_id: int, note_id: str, title: str, ip: str, ua: str):
    await publish_event("note_created", user_id=user_id, resource_id=note_id,
                        ip_address=ip, user_agent=ua, metadata={"title": title})


async def event_note_updated(user_id: int, note_id: str, ip: str, ua: str):
    await publish_event("note_updated", user_id=user_id, resource_id=note_id,
                        ip_address=ip, user_agent=ua)


async def event_note_viewed(user_id: Optional[int], guest_id: Optional[str],
                             note_id: str, ip: str, ua: str):
    await publish_event("note_viewed", user_id=user_id, guest_id=guest_id,
                        resource_id=note_id, ip_address=ip, user_agent=ua)


async def event_note_trashed(user_id: int, note_id: str, ip: str, ua: str):
    await publish_event("note_deleted", user_id=user_id, resource_id=note_id,
                        ip_address=ip, user_agent=ua)


async def event_note_restored(user_id: int, note_id: str, ip: str, ua: str):
    await publish_event("note_restored", user_id=user_id, resource_id=note_id,
                        ip_address=ip, user_agent=ua)


async def event_note_searched(user_id: int, query: str, ip: str, ua: str):
    await publish_event("note_searched", user_id=user_id, ip_address=ip,
                        user_agent=ua, metadata={"query": query})


async def event_invite_sent(user_id: int, note_id: str, invitee_email: str,
                             permission: str, ip: str, ua: str):
    await publish_event("invite_sent", user_id=user_id, resource_id=note_id,
                        ip_address=ip, user_agent=ua,
                        metadata={"invitee": invitee_email, "permission": permission})


async def event_invite_accepted(user_id: int, note_id: str, ip: str, ua: str):
    await publish_event("invite_accepted", user_id=user_id, resource_id=note_id,
                        ip_address=ip, user_agent=ua)


async def event_guest_visit(guest_id: str, path: str, ip: str, ua: str):
    await publish_event("guest_view", guest_id=guest_id, ip_address=ip,
                        user_agent=ua, metadata={"path": path})


async def event_infra(event_type: str, metadata: dict):
    """For infra-level events: broker down, nginx restart, crash tests."""
    await publish_event(event_type, metadata=metadata)
