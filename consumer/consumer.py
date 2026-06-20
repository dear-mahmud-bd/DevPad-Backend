"""
consumer/consumer.py

Standalone Kafka consumer process.
Run with: python -m consumer.consumer

Reads from the devpad_events topic and writes each event
as a document to MongoDB's activity_logs collection.

Why a separate process (not a FastAPI background task)?
See ADL-3 in requirements.md:
- Events survive API restarts (Kafka offset is committed)
- Consumer can be scaled independently
- No coupling between logging and request handling
"""
import asyncio
import json
import logging
import os
import signal
import sys

from aiokafka import AIOKafkaConsumer
from motor.motor_asyncio import AsyncIOMotorClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | consumer | %(message)s",
)
logger = logging.getLogger(__name__)

# Config from environment (same .env file as the API)
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka1:9092,kafka2:9093,kafka3:9094")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "devpad_events")
KAFKA_GROUP = os.getenv("KAFKA_CONSUMER_GROUP", "devpad_consumer_group")
MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://mongo:mongo_secret@mongodb:27017")
MONGODB_DB = os.getenv("MONGODB_DB_NAME", "devpad_db")

_running = True


def handle_signal(sig, frame):
    """Graceful shutdown on SIGTERM / SIGINT."""
    global _running
    logger.info("Shutdown signal received.")
    _running = False


signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)


async def main():
    mongo_client = AsyncIOMotorClient(MONGODB_URL)
    db = mongo_client[MONGODB_DB]
    activity_col = db["activity_logs"]

    consumer = AIOKafkaConsumer(
        KAFKA_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP,
        group_id=KAFKA_GROUP,
        # earliest: on first start (or after reset), read all unread messages
        # latest: skip old messages if we restart mid-stream
        auto_offset_reset="earliest",
        enable_auto_commit=False,   # manual commit = no message loss on crash
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
    )

    await consumer.start()
    logger.info("Consumer started. Listening on topic '%s'...", KAFKA_TOPIC)

    try:
        async for msg in consumer:
            if not _running:
                break

            event = msg.value
            logger.info("Event received: %s [user=%s]", event.get("event_type"), event.get("user_id"))

            try:
                await activity_col.insert_one(event)
                # Commit offset only after successful write — guarantees at-least-once delivery
                await consumer.commit()
            except Exception as e:
                logger.error("Failed to write event to MongoDB: %s", e)
                # Do NOT commit offset — message will be redelivered on restart
    finally:
        await consumer.stop()
        mongo_client.close()
        logger.info("Consumer shut down cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
