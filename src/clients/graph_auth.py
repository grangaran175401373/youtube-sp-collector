from __future__ import annotations

import msal


GRAPH_DEFAULT_SCOPE = ["https://graph.microsoft.com/.default"]


class GraphAuthError(RuntimeError):
    """Microsoft Graph 認証エラー."""


class GraphAuthClient:
    """MSAL を用いた Client Credentials 認証クライアント."""

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
    ) -> None:
        self.tenant_id = tenant_id
        self.client_id = client_id
        self._app = msal.ConfidentialClientApplication(
            client_id=client_id,
            client_credential=client_secret,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
        )

    def get_access_token(self) -> str:
        """有効な Microsoft Graph アクセストークンを返す（MSALキャッシュ利用）."""
        result = self._app.acquire_token_for_client(scopes=GRAPH_DEFAULT_SCOPE)
        if not result or "access_token" not in result:
            error = (result or {}).get("error", "unknown_error")
            desc = (result or {}).get("error_description", "No error description")
            raise GraphAuthError(f"Graphトークン取得失敗 ({error}): {desc}")
        return str(result["access_token"])
