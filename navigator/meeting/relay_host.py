"""Where the local screenshare relay listens, and which host Attendee dials.

Rootful Docker: the Attendee bridge gateway is an address on the host, so the
relay binds it and the webpage-streamer fetches ``http://<gateway>:<port>/view``.

Rootless Docker (slirp4netns / pasta): that gateway exists only inside the
rootless network namespace. Binding it raises ``OSError`` 99
(``EADDRNOTAVAIL``). The relay then listens on ``0.0.0.0``. Containers still
cannot use the gateway IP to reach the host, so the URL host has to be
something the container can route: ``host.docker.internal`` (with
``extra_hosts: ["host.docker.internal:host-gateway"]`` on the streamer) or
slirp4netns' ``10.0.2.2``. Set that with ``NAVIGATOR_RELAY_PUBLIC_HOST``.
"""

from __future__ import annotations

import errno
import subprocess
from collections.abc import Callable
from typing import Any

from navigator.core.settings import settings

DEFAULT_ATTENDEE_DOCKER_NETWORK = "attendee_attendee_network"
FALLBACK_BIND_HOST = "0.0.0.0"
_UNUSABLE_PUBLIC_HOSTS = frozenset({"", "0.0.0.0", "::", "[::]"})


def attendee_network_name() -> str:
    """``NAVIGATOR_ATTENDEE_DOCKER_NETWORK``, or the stock compose network."""
    name = (settings.attendee_docker_network or "").strip()
    return name or DEFAULT_ATTENDEE_DOCKER_NETWORK


def docker_network_gateway(network: str) -> str | None:
    """Gateway IP of ``network``, or None when docker cannot report one.

    Does not open a listening socket. A missing docker binary, a missing
    network, or an empty gateway all return None so the caller can fall back.
    """
    name = (network or "").strip() or DEFAULT_ATTENDEE_DOCKER_NETWORK
    try:
        proc = subprocess.run(
            [
                "docker",
                "network",
                "inspect",
                name,
                "-f",
                "{{(index .IPAM.Config 0).Gateway}}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    gateway = (proc.stdout or "").strip()
    if not gateway or gateway == "<no value>":
        return None
    return gateway


def local_screenshare_url(host: str, port: int) -> str:
    """URL the Attendee webpage-streamer loads. ``host`` must be dialable from the container."""
    return f"http://{host}:{port}/view"


def _addr_not_avail(exc: BaseException) -> bool:
    if not isinstance(exc, OSError):
        return False
    if exc.errno in (errno.EADDRNOTAVAIL, 99):
        return True
    return "cannot assign requested address" in str(exc).lower()


def _usable_public_host(raw: str) -> str:
    host = (raw or "").strip()
    if host in _UNUSABLE_PUBLIC_HOSTS:
        return ""
    return host


def _warn(message: str) -> None:
    print(f"[live] WARN: {message}", flush=True)


def _warn_public_host_unset() -> None:
    _warn(
        "screenshare relay is listening on 0.0.0.0 and NAVIGATOR_RELAY_PUBLIC_HOST "
        "is unset. Attendee containers cannot dial 0.0.0.0 or the docker bridge "
        "gateway on rootless Docker. Set NAVIGATOR_RELAY_PUBLIC_HOST to "
        "host.docker.internal (webpage-streamer extra_hosts: "
        '"host.docker.internal:host-gateway") or to 10.0.2.2 for rootless '
        "slirp4netns. Screenshare will use the public tunnel until that host is set."
    )


def open_screenshare_relay(
    *,
    meeting_url: str | None,
    gateway: str | None,
    local_enabled: bool = True,
    start: Callable[..., Any] | None = None,
) -> tuple[Any, str | None]:
    """Start the screenshare relay. Return ``(handle, url_host)``.

    ``url_host`` is None when the screenshare URL should stay on the public
    tunnel. ``start`` defaults to :func:`navigator.meeting.relay.start_relay`
    and is injectable so tests never bind a socket.

    ``NAVIGATOR_SCREENSHARE_LOCAL=0`` keeps the loopback relay and the tunnel,
    unless ``NAVIGATOR_RELAY_BIND_HOST`` is set explicitly.
    """
    if start is None:
        from navigator.meeting.relay import start_relay

        start = start_relay

    public = _usable_public_host(settings.relay_public_host or "")
    override = (settings.relay_bind_host or "").strip()

    if not local_enabled and not override:
        return start(meeting_url=meeting_url), None

    if override:
        bind_host = override
        detail = f"NAVIGATOR_RELAY_BIND_HOST={override}"
    elif gateway:
        bind_host = gateway
        detail = f"docker gateway {gateway} on {attendee_network_name()}"
    else:
        _warn(
            f"Attendee docker network {attendee_network_name()!r} was not found "
            "(set NAVIGATOR_ATTENDEE_DOCKER_NETWORK when the compose project is "
            f"not 'attendee'); screenshare relay listening on {FALLBACK_BIND_HOST}"
        )
        bind_host = FALLBACK_BIND_HOST
        detail = ""

    if bind_host != FALLBACK_BIND_HOST:
        try:
            relay = start(host=bind_host, meeting_url=meeting_url)
        except OSError as exc:
            if not _addr_not_avail(exc):
                raise
            _warn(
                f"screenshare relay cannot bind {detail} ({exc}); "
                f"listening on {FALLBACK_BIND_HOST}. On rootless Docker the "
                "network gateway is not an address on the host."
            )
            relay = start(host=FALLBACK_BIND_HOST, meeting_url=meeting_url)
            bind_host = FALLBACK_BIND_HOST
    else:
        relay = start(host=FALLBACK_BIND_HOST, meeting_url=meeting_url)

    if public:
        return relay, public
    if bind_host == FALLBACK_BIND_HOST:
        _warn_public_host_unset()
        return relay, None
    return relay, bind_host
