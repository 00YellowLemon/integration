"""Google Cloud Pub/Sub setup and helper utilities using Application Default Credentials (ADC)."""
import logging
from typing import Optional, Tuple
import google.auth
from google.cloud import pubsub_v1

from app.core.config import get_settings

logger = logging.getLogger("gmail_assistant.pubsub")


class PubSubHelper:
    """Helper for interacting with Google Cloud Pub/Sub using Application Default Credentials."""

    @staticmethod
    def get_credentials_and_project() -> Tuple[Optional[google.auth.credentials.Credentials], Optional[str]]:
        """Retrieve Google ADC credentials and default GCP project ID."""
        settings = get_settings()
        try:
            creds, project = google.auth.default()
            proj = settings.GCP_PROJECT_ID or project
            return creds, proj
        except Exception as e:
            logger.warning("Could not resolve Google Application Default Credentials: %s", e)
            return None, settings.GCP_PROJECT_ID

    @classmethod
    def get_project_id(cls) -> str:
        """Resolve effective GCP Project ID."""
        settings = get_settings()
        if settings.GCP_PROJECT_ID:
            return settings.GCP_PROJECT_ID
        _, project = cls.get_credentials_and_project()
        return project or ""

    @classmethod
    def get_publisher_client(cls) -> pubsub_v1.PublisherClient:
        """Initialize Pub/Sub PublisherClient with ADC."""
        creds, _ = cls.get_credentials_and_project()
        if creds:
            return pubsub_v1.PublisherClient(credentials=creds)
        return pubsub_v1.PublisherClient()

    @classmethod
    def get_subscriber_client(cls) -> pubsub_v1.SubscriberClient:
        """Initialize Pub/Sub SubscriberClient with ADC."""
        creds, _ = cls.get_credentials_and_project()
        if creds:
            return pubsub_v1.SubscriberClient(credentials=creds)
        return pubsub_v1.SubscriberClient()

    @classmethod
    def get_topic_path(
        cls,
        project_id: Optional[str] = None,
        topic_name: Optional[str] = None,
    ) -> str:
        """Build fully-qualified Pub/Sub topic path: projects/{project}/topics/{topic}."""
        settings = get_settings()
        proj = project_id or cls.get_project_id()
        topic = topic_name or settings.PUBSUB_TOPIC_NAME
        if not proj:
            raise ValueError("GCP Project ID is required to format Pub/Sub topic path.")
        return f"projects/{proj}/topics/{topic}"

    @classmethod
    def get_subscription_path(
        cls,
        project_id: Optional[str] = None,
        subscription_name: Optional[str] = None,
    ) -> str:
        """Build fully-qualified Pub/Sub subscription path: projects/{project}/subscriptions/{sub}."""
        settings = get_settings()
        proj = project_id or cls.get_project_id()
        sub = subscription_name or settings.PUBSUB_SUBSCRIPTION_NAME
        if not proj:
            raise ValueError("GCP Project ID is required to format Pub/Sub subscription path.")
        return f"projects/{proj}/subscriptions/{sub}"

    @classmethod
    def ensure_topic_and_subscription(
        cls,
        project_id: Optional[str] = None,
        push_endpoint: Optional[str] = None,
    ) -> dict:
        """
        Verify or create Pub/Sub topic and optional push subscription.
        Returns a dictionary containing topic and subscription paths.
        """
        proj = project_id or cls.get_project_id()
        topic_path = cls.get_topic_path(proj)
        sub_path = cls.get_subscription_path(proj)

        publisher = cls.get_publisher_client()
        try:
            publisher.get_topic(request={"topic": topic_path})
            logger.info("Pub/Sub topic already exists: %s", topic_path)
        except Exception:
            try:
                publisher.create_topic(request={"name": topic_path})
                logger.info("Created Pub/Sub topic: %s", topic_path)
            except Exception as e:
                logger.warning("Could not verify or create Pub/Sub topic %s: %s", topic_path, e)

        subscriber = cls.get_subscriber_client()
        try:
            subscriber.get_subscription(request={"subscription": sub_path})
            logger.info("Pub/Sub subscription already exists: %s", sub_path)
        except Exception:
            try:
                sub_request: dict = {
                    "name": sub_path,
                    "topic": topic_path,
                }
                if push_endpoint:
                    sub_request["push_config"] = {"push_endpoint": push_endpoint}
                subscriber.create_subscription(request=sub_request)
                logger.info("Created Pub/Sub subscription: %s", sub_path)
            except Exception as e:
                logger.warning("Could not verify or create Pub/Sub subscription %s: %s", sub_path, e)

        return {
            "topic": topic_path,
            "subscription": sub_path,
        }
