"""Gmail REST API client with strictly read-only scope enforcement and async execution."""
import asyncio
from typing import Any, Dict, List, Optional
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.core.retry import with_retry


FORBIDDEN_WRITE_SCOPES = {
    "https://mail.google.com/",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.insert",
    "https://www.googleapis.com/auth/gmail.labels",
}


class GmailClient:
    """Async-compatible Gmail API client strictly enforced to read-only operations."""

    def __init__(self, credentials: Credentials):
        self.credentials = credentials
        self._validate_read_only_safety()

    def _validate_read_only_safety(self) -> None:
        """
        Validate that the provided credentials do not contain any write, compose,
        or delete scopes, enforcing Constitutional Principle 1 (Least Privilege & Read-Only Safety).
        """
        granted_scopes = set(self.credentials.scopes or [])
        forbidden_matches = granted_scopes.intersection(FORBIDDEN_WRITE_SCOPES)
        if forbidden_matches:
            raise PermissionError(
                f"Security violation: Forbidden write scopes detected in Gmail credentials: {forbidden_matches}. "
                "The Gmail AI Assistant operates strictly in read-only mode."
            )

    def _get_service(self):
        """Construct synchronous Google API client discovery service."""
        return build("gmail", "v1", credentials=self.credentials, cache_discovery=False)

    def _sync_get_profile(self, user_id: str = "me") -> Dict[str, Any]:
        """Synchronous call to Gmail API users.getProfile."""
        service = self._get_service()
        profile = service.users().getProfile(userId=user_id).execute()
        return {
            "emailAddress": profile.get("emailAddress", ""),
            "messagesTotal": int(profile.get("messagesTotal", 0)),
            "threadsTotal": int(profile.get("threadsTotal", 0)),
            "historyId": str(profile.get("historyId", "")),
        }

    @with_retry(max_attempts=4, min_wait=1.0, max_wait=30.0)
    async def get_profile(self, user_id: str = "me") -> Dict[str, Any]:
        """
        Asynchronously fetch the Gmail user profile and mailbox statistics.
        Returns dictionary with emailAddress, messagesTotal, threadsTotal, and historyId.
        """
        return await asyncio.to_thread(self._sync_get_profile, user_id=user_id)

    def _sync_list_threads(
        self,
        max_results: int = 50,
        page_token: Optional[str] = None,
        user_id: str = "me",
    ) -> Dict[str, Any]:
        """Synchronous call to Gmail API users.threads.list."""
        service = self._get_service()
        req = service.users().threads().list(
            userId=user_id,
            maxResults=max_results,
            pageToken=page_token,
        )
        return req.execute()

    @with_retry(max_attempts=4, min_wait=1.0, max_wait=30.0)
    async def list_threads(
        self,
        max_results: int = 50,
        page_token: Optional[str] = None,
        user_id: str = "me",
    ) -> Dict[str, Any]:
        """Asynchronously list thread summaries from the Gmail mailbox."""
        return await asyncio.to_thread(
            self._sync_list_threads,
            max_results=max_results,
            page_token=page_token,
            user_id=user_id,
        )

    def _sync_get_thread(self, thread_id: str, user_id: str = "me") -> Dict[str, Any]:
        """Synchronous call to Gmail API users.threads.get with full format."""
        service = self._get_service()
        return service.users().threads().get(userId=user_id, id=thread_id, format="full").execute()

    @with_retry(max_attempts=4, min_wait=1.0, max_wait=30.0)
    async def get_thread(self, thread_id: str, user_id: str = "me") -> Dict[str, Any]:
        """Asynchronously fetch complete thread payload with full message details."""
        return await asyncio.to_thread(self._sync_get_thread, thread_id=thread_id, user_id=user_id)

    def _sync_get_message(self, message_id: str, user_id: str = "me") -> Dict[str, Any]:
        """Synchronous call to Gmail API users.messages.get with full format."""
        service = self._get_service()
        return service.users().messages().get(userId=user_id, id=message_id, format="full").execute()

    @with_retry(max_attempts=4, min_wait=1.0, max_wait=30.0)
    async def get_message(self, message_id: str, user_id: str = "me") -> Dict[str, Any]:
        """Asynchronously fetch full message payload."""
        return await asyncio.to_thread(self._sync_get_message, message_id=message_id, user_id=user_id)

    def _sync_list_history(
        self,
        start_history_id: str,
        page_token: Optional[str] = None,
        user_id: str = "me",
    ) -> Dict[str, Any]:
        """Synchronous call to Gmail API users.history.list."""
        service = self._get_service()
        return service.users().history().list(
            userId=user_id,
            startHistoryId=start_history_id,
            pageToken=page_token,
        ).execute()

    @with_retry(max_attempts=4, min_wait=1.0, max_wait=30.0)
    async def list_history(
        self,
        start_history_id: str,
        page_token: Optional[str] = None,
        user_id: str = "me",
    ) -> Dict[str, Any]:
        """Asynchronously list mailbox history records starting from a historyId."""
        return await asyncio.to_thread(
            self._sync_list_history,
            start_history_id=start_history_id,
            page_token=page_token,
            user_id=user_id,
        )

    def _sync_register_watch(
        self,
        topic_name: str,
        label_ids: Optional[List[str]] = None,
        user_id: str = "me",
    ) -> Dict[str, Any]:
        """Synchronous call to Gmail API users.watch."""
        service = self._get_service()
        body: Dict[str, Any] = {"topicName": topic_name}
        if label_ids:
            body["labelIds"] = label_ids
        return service.users().watch(userId=user_id, body=body).execute()

    @with_retry(max_attempts=4, min_wait=1.0, max_wait=30.0)
    async def register_watch(
        self,
        topic_name: str,
        label_ids: Optional[List[str]] = None,
        user_id: str = "me",
    ) -> Dict[str, Any]:
        """Asynchronously register a Pub/Sub topic to receive mailbox push notifications."""
        return await asyncio.to_thread(
            self._sync_register_watch,
            topic_name=topic_name,
            label_ids=label_ids,
            user_id=user_id,
        )

    def _sync_stop_watch(self, user_id: str = "me") -> Dict[str, Any]:
        """Synchronous call to Gmail API users.stop."""
        service = self._get_service()
        return service.users().stop(userId=user_id).execute()

    @with_retry(max_attempts=4, min_wait=1.0, max_wait=30.0)
    async def stop_watch(self, user_id: str = "me") -> Dict[str, Any]:
        """Asynchronously stop mailbox push notifications."""
        return await asyncio.to_thread(self._sync_stop_watch, user_id=user_id)

