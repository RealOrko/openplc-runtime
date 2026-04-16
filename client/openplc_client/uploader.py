"""Handles the HTTPS conversation with the runtime's REST API:
    GET  /api/get-users-info   — detect empty user DB
    POST /api/create-user      — bootstrap first admin (only when empty)
    POST /api/login            — obtain JWT
    POST /api/upload-file      — multipart upload
    GET  /api/compilation-status — poll until SUCCESS / FAILED
"""

from __future__ import annotations

import time
import warnings
from dataclasses import dataclass
from pathlib import Path

import requests
import urllib3


def _silence_self_signed_warnings() -> None:
    warnings.filterwarnings("ignore", category=urllib3.exceptions.InsecureRequestWarning)


_silence_self_signed_warnings()


COMPILE_POLL_INTERVAL_S = 1.0
COMPILE_TIMEOUT_S = 300  # 5 minutes — matches the editor


@dataclass
class RuntimeClient:
    base_url: str
    username: str
    password: str
    timeout: float = 15.0
    _token: str | None = None

    @property
    def _headers(self) -> dict[str, str]:
        if not self._token:
            raise RuntimeError("Not authenticated — call login() first")
        return {"Authorization": f"Bearer {self._token}"}

    def _url(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}{path}"

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        kwargs.setdefault("verify", False)
        kwargs.setdefault("timeout", self.timeout)
        return requests.request(method, self._url(path), **kwargs)

    def ping(self) -> None:
        """Best-effort reachability check — we don't require a 200 because
        /api/ping itself is JWT-protected; any TLS response means the server
        is up."""
        try:
            self._request("GET", "/api/get-users-info")
        except requests.exceptions.ConnectionError as e:
            raise RuntimeError(
                f"Cannot reach runtime at {self.base_url}: {e}"
            ) from e

    def users_exist(self) -> bool:
        """True if the runtime already has at least one user registered."""
        resp = self._request("GET", "/api/get-users-info")
        if resp.status_code == 404:
            return False
        if resp.status_code == 200:
            return True
        # 401 without JWT means users exist and auth is required
        if resp.status_code in (401, 422):
            return True
        resp.raise_for_status()
        return True

    def bootstrap_first_user(self) -> None:
        """Create the first admin user when the DB is empty. This endpoint is
        only open when no users exist; subsequent calls require a JWT."""
        resp = self._request(
            "POST",
            "/api/create-user",
            json={"username": self.username, "password": self.password, "role": "admin"},
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"Failed to bootstrap first user ({resp.status_code}): {resp.text}"
            )
        print(f"[auth] bootstrapped first admin user '{self.username}'")

    def login(self) -> None:
        resp = self._request(
            "POST",
            "/api/login",
            json={"username": self.username, "password": self.password},
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Login failed ({resp.status_code}): {resp.text}"
            )
        data = resp.json()
        token = data.get("access_token") or data.get("token")
        if not token:
            raise RuntimeError(f"Login response missing access_token: {data}")
        self._token = token
        print(f"[auth] logged in as '{self.username}'")

    def ensure_authenticated(self) -> None:
        """One-shot: if no users exist, bootstrap; then login. This matches
        the first-connect flow the editor uses."""
        self.ping()
        if not self.users_exist():
            self.bootstrap_first_user()
        self.login()

    def upload_zip(self, zip_path: Path) -> None:
        with zip_path.open("rb") as f:
            files = {"file": (zip_path.name, f, "application/zip")}
            resp = self._request(
                "POST",
                "/api/upload-file",
                headers=self._headers,
                files=files,
                timeout=60.0,
            )

        if resp.status_code != 200:
            raise RuntimeError(
                f"Upload failed ({resp.status_code}): {resp.text}"
            )
        body = resp.json()
        if body.get("UploadFileFail"):
            raise RuntimeError(f"Runtime rejected zip: {body['UploadFileFail']}")
        print(f"[upload] runtime accepted zip ({zip_path.stat().st_size} bytes); "
              f"status={body.get('CompilationStatus', 'UNKNOWN')}")

    def poll_compilation(self) -> None:
        """Poll /api/compilation-status until SUCCESS, FAILED, or timeout.
        Streams each new log line as the runtime produces it."""
        deadline = time.monotonic() + COMPILE_TIMEOUT_S
        seen_logs = 0

        while time.monotonic() < deadline:
            resp = self._request(
                "GET",
                "/api/compilation-status",
                headers=self._headers,
            )
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Status poll failed ({resp.status_code}): {resp.text}"
                )
            body = resp.json()
            logs = body.get("logs", [])
            if len(logs) > seen_logs:
                for line in logs[seen_logs:]:
                    print(f"[runtime] {line.rstrip()}")
                seen_logs = len(logs)

            status = body.get("status", "UNKNOWN")
            if status == "SUCCESS":
                print("[compile] runtime build SUCCESS — PLC program restarted")
                return
            if status == "FAILED":
                exit_code = body.get("exit_code")
                raise RuntimeError(
                    f"Runtime build FAILED (exit_code={exit_code}). See logs above."
                )

            time.sleep(COMPILE_POLL_INTERVAL_S)

        raise TimeoutError(
            f"Compilation did not finish within {COMPILE_TIMEOUT_S}s"
        )

    # ---- Observability helpers ---------------------------------------------

    def plc_status(self, include_stats: bool = True) -> dict:
        params = {"include_stats": "true"} if include_stats else None
        resp = self._request(
            "GET",
            "/api/status",
            headers=self._headers,
            params=params,
        )
        resp.raise_for_status()
        return resp.json()

    def compilation_status(self) -> dict:
        resp = self._request(
            "GET",
            "/api/compilation-status",
            headers=self._headers,
        )
        resp.raise_for_status()
        return resp.json()

    def runtime_logs(self, since_id: int | None = None, level: str | None = None) -> list:
        params = {}
        if since_id is not None:
            params["id"] = since_id
        if level is not None:
            params["level"] = level
        resp = self._request(
            "GET",
            "/api/runtime-logs",
            headers=self._headers,
            params=params or None,
        )
        resp.raise_for_status()
        body = resp.json()
        return body.get("runtime-logs", []) or []

    def start_plc(self) -> dict:
        resp = self._request("GET", "/api/start-plc", headers=self._headers)
        resp.raise_for_status()
        return resp.json()

    def stop_plc(self) -> dict:
        resp = self._request("GET", "/api/stop-plc", headers=self._headers)
        resp.raise_for_status()
        return resp.json()
