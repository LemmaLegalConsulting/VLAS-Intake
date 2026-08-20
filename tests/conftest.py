import os
import socket
import subprocess
import urllib.parse
import urllib.request
from unittest.mock import MagicMock

import aiohttp
import dotenv
import httpx
import pytest

EXTERNAL_CREDENTIALS = (
    "DIALPAD_API_KEY",
    "DIALPAD_SMS_NUMBER",
    "LEGAL_SERVER_SUBDOMAIN",
    "LEGAL_SERVER_BEARER_TOKEN",
    "AZURE_API_KEY",
    "AZURE_LLM_ENDPOINT",
    "OPENAI_API_KEY",
    "DAILY_API_KEY",
    "DEEPGRAM_API_KEY",
)


def _is_loopback_address(host: str) -> bool:
    """Check if a host resolves to a loopback address."""
    return host in ("127.0.0.1", "::1", "localhost", "0.0.0.0")


# ---------------------------------------------------------------------------
# Network-blocking fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def block_outbound_network(monkeypatch):
    """Block all non-loopback outbound network access during tests.

    Tests that intentionally use loopback connections or fake transports
    can opt out by decorating with ``@pytest.mark.no_network_block`` or by
    supplying an explicit ``monkeypatch.setattr`` override *before* the test
    accesses the network module.
    """
    for name in EXTERNAL_CREDENTIALS:
        monkeypatch.delenv(name, raising=False)

    # Neutralise .env loading so tests never read the real .env file.
    monkeypatch.setattr(dotenv, "load_dotenv", lambda **kw: None)

    # ---- aiohttp ----
    def _blocked_aiohttp_session(*args, **kwargs):
        raise AssertionError(
            "Outbound aiohttp.ClientSession is disabled during tests. "
            "Install an explicit fake via monkeypatch.setattr."
        )

    monkeypatch.setattr(aiohttp, "ClientSession", _blocked_aiohttp_session)

    # ---- httpx (used by openai / azure SDK) ----
    original_httpx_request = httpx.Client.request

    def _blocked_httpx_request(self, method, url, *args, **kwargs):
        host = urllib.parse.urlparse(str(url)).hostname or ""
        if not _is_loopback_address(host):
            raise AssertionError(
                f"Outbound httpx request to {url} is disabled during tests. "
                "Install an explicit mock via monkeypatch.setattr."
            )
        return original_httpx_request(self, method, url, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "request", _blocked_httpx_request)

    # ---- httpx async (used by openai / azure SDK) ----
    original_async_httpx_request = httpx.AsyncClient.request

    async def _blocked_async_httpx_request(self, method, url, *args, **kwargs):
        host = urllib.parse.urlparse(str(url)).hostname or ""
        if not _is_loopback_address(host):
            raise AssertionError(
                f"Outbound async httpx request to {url} is disabled during tests. "
                "Install an explicit mock via monkeypatch.setattr."
            )
        return await original_async_httpx_request(self, method, url, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "request", _blocked_async_httpx_request)

    # ---- raw sockets ----
    original_socket_connect = socket.socket.connect

    def _blocked_socket_connect(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else address
        if not _is_loopback_address(host):
            raise AssertionError(
                f"Outbound socket connect to {address} is disabled during tests. "
                "Use a loopback address or monkeypatch the socket module."
            )
        return original_socket_connect(self, address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", _blocked_socket_connect)

    # ---- DNS ----
    def _blocked_getaddrinfo(host, port, *args, **kwargs):
        if not _is_loopback_address(host):
            raise AssertionError(
                f"DNS lookup for {host} is disabled during tests. "
                "Use an IP address or monkeypatch socket.getaddrinfo."
            )
        return original_getaddrinfo(host, port, *args, **kwargs)

    original_getaddrinfo = socket.getaddrinfo
    monkeypatch.setattr(socket, "getaddrinfo", _blocked_getaddrinfo)

    # ---- urllib ----
    original_urlopen = urllib.request.urlopen

    def _blocked_urlopen(url, *args, **kwargs):
        parsed = urllib.parse.urlparse(str(url))
        if not _is_loopback_address(parsed.hostname or ""):
            raise AssertionError(
                f"Outbound urllib request to {url} is disabled during tests. "
                "Use an explicit mock via monkeypatch.setattr."
            )
        return original_urlopen(url, *args, **kwargs)

    monkeypatch.setattr(urllib.request, "urlopen", _blocked_urlopen)

    # ---- websockets library ----
    try:
        import websockets

        original_ws_connect = websockets.connect

        def _blocked_ws_connect(uri, *args, **kwargs):
            parsed = urllib.parse.urlparse(str(uri))
            if not _is_loopback_address(parsed.hostname or ""):
                raise AssertionError(
                    f"Outbound WebSocket connection to {uri} is disabled during tests. "
                    "Use an explicit mock via monkeypatch.setattr."
                )
            return original_ws_connect(uri, *args, **kwargs)

        monkeypatch.setattr(websockets, "connect", _blocked_ws_connect)
    except ImportError:
        pass

    # ---- subprocess: block known network-capable commands ----
    NETWORK_COMMANDS = frozenset(
        {
            "ping",
            "ping6",
            "traceroute",
            "traceroute6",
            "tracert",
            "nslookup",
            "dig",
            "host",
            "whois",
            "curl",
            "wget",
            "fetch",
            "httpie",
            "nc",
            "ncat",
            "netcat",
            "socat",
            "ssh",
            "scp",
            "rsync",
            "telnet",
            "ftp",
            "sftp",
            "iwconfig",
            "ifconfig",
            "ip",
            "nmap",
            "masscan",
        }
    )

    original_popen_run = subprocess.Popen.__init__

    def _blocked_popen_init(self, args, **kwargs):
        # For shell strings, scan all space-delimited tokens for network commands
        # (catches shell pipelines like "echo | ping 8.8.8.8")
        if isinstance(args, str):
            for token in str(args).split():
                cleaned = token.strip("|;&$><()`'\"").lower().strip()
                if cleaned in NETWORK_COMMANDS:
                    raise AssertionError(
                        f"Subprocess command involves network-capable tool "
                        f"'{cleaned}' and is blocked during tests. "
                        f"Use @pytest.mark.allow_subprocess or override "
                        f"monkeypatch."
                    )

        # Check the primary executable, normalising absolute paths
        cmd = args if isinstance(args, (list, tuple)) else [args]
        executable = ""
        if cmd:
            head = (
                str(cmd[0]).lower().split()[0]
                if isinstance(cmd[0], str)
                else str(cmd[0]).lower()
            )
            executable = os.path.basename(head)
        if executable in NETWORK_COMMANDS:
            raise AssertionError(
                f"Subprocess command '{executable}' is a network-capable tool "
                f"and is blocked during tests. "
                f"Use @pytest.mark.allow_subprocess or override monkeypatch."
            )
        return original_popen_run(self, args, **kwargs)

    monkeypatch.setattr(subprocess.Popen, "__init__", _blocked_popen_init)

    yield


# ---------------------------------------------------------------------------
# Convenience: allow loopback-only HTTP / socket access via mark
# ---------------------------------------------------------------------------


@pytest.fixture
def loopback_socket(monkeypatch):
    """Allow the test to use loopback sockets without the block.

    Restores the original socket.connect for tests that need it.
    """
    _restore = getattr(socket.socket, "connect", None)
    if _restore is not None and hasattr(_restore, "__wrapped__"):
        monkeypatch.setattr(socket.socket, "connect", _restore.__wrapped__)
    yield


@pytest.fixture
def loopback_aiohttp(monkeypatch):
    """Allow aiohttp to loopback addresses while still blocking external access."""
    import aiohttp as _aiohttp

    # Restore the real ClientSession class (replaces the blanket block)
    monkeypatch.setattr(_aiohttp, "ClientSession", _aiohttp.ClientSession)

    # Gate _request so only loopback destinations are permitted
    _orig_request = _aiohttp.ClientSession._request

    async def _loopback_gated_request(self, method, str_or_url, *args, **kwargs):
        url = str(str_or_url)
        host = urllib.parse.urlparse(url).hostname or ""
        if not _is_loopback_address(host):
            raise AssertionError(
                f"Outbound aiohttp request to {url} is disabled during tests. "
                "Use the fake_aiohttp_session fixture or monkeypatch.setattr."
            )
        return await _orig_request(self, method, str_or_url, *args, **kwargs)

    monkeypatch.setattr(_aiohttp.ClientSession, "_request", _loopback_gated_request)
    yield


@pytest.fixture
def fake_aiohttp_session(monkeypatch):
    """Install a MagicMock for aiohttp.ClientSession so tests can provide a fake."""
    fake = MagicMock()
    monkeypatch.setattr(aiohttp, "ClientSession", fake)
    yield fake


@pytest.fixture
def fake_httpx_client(monkeypatch):
    """Install a MagicMock for httpx.Client so tests can provide a fake."""
    fake = MagicMock()
    monkeypatch.setattr(httpx, "Client", fake)
    yield fake
