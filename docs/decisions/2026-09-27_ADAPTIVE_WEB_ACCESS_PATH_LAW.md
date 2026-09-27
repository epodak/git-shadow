# ADR 2026-09-27 — Adaptive Web Access Path Law

## Status

Accepted.

## Problem

git-shadow previously coupled CloudCLI with one personal public hostname. That
mixed three independent concepts:

1. the CloudCLI application process;
2. the private network path to the VPS;
3. a public reverse-proxy/domain path.

A personal Cloudflare hostname is useful for one deployment, but it must not be
a product default.

## Decision

CloudCLI remains a localhost-only application:

~~~text
CloudCLI
127.0.0.1:3001
~~~

Browser access is resolved by a separate Access Path layer:

~~~text
                     ┌─ Tailscale Serve ─→ private tailnet HTTPS
CloudCLI localhost ──┤
                     └─ configured public URL ─→ e.g. Cloudflare Tunnel
~~~

No personal hostname is compiled into git-shadow.

## Tailscale detection

The controller reads local tailscale status JSON and, through the existing SSH
control connection, reads the remote node's tailscale status JSON.

A remote node is treated as a usable tailnet peer only when its Tailscale IP or
DNS identity appears in the local peer map.

The resolver reports the observed connection type when available:

~~~text
direct
peer-relay
derp
unknown
~~~

The status JSON format is treated as observational input, not durable state.

## Automatic selection policy

--access auto is the default.

~~~text
same tailnet + direct       -> Tailscale Serve
same tailnet + peer-relay   -> Tailscale Serve
same tailnet + DERP
    + public URL configured -> public URL
same tailnet + DERP
    + no public URL         -> Tailscale Serve
Tailscale unavailable
    + public URL configured -> public URL
neither available           -> Web degrades; Git/Shadow remain operational
~~~

This is deliberately a policy heuristic, not a claim that either transport is
always faster.

Users may force a path:

~~~text
--access tailscale
--access public
~~~

## Tailscale Serve

When a visible tailnet peer is selected, git-shadow may run:

~~~bash
tailscale serve --bg 3001
~~~

on the remote host.

This exposes the localhost CloudCLI service through the remote node's tailnet
HTTPS name while keeping the application itself bound to localhost.

## Public URL / Cloudflare Tunnel

The public path is configuration, not product identity.

Accepted configuration:

~~~text
--cloudcli-public-url https://cli.example.com

GIT_SHADOW_CLOUDFLARE_URL=https://cli.example.com
GIT_SHADOW_CLOUDCLI_PUBLIC_URL=https://cli.example.com  # legacy env name
~~~

git-shadow does not create or mutate Cloudflare Tunnel configuration in the
normal CloudCLI run path.

A public URL remains valuable for:

- access from devices that are not members of the tailnet;
- a stable custom domain;
- Cloudflare Access/WAF/DDoS controls when configured;
- fallback when the current Tailscale route is DERP-only or unavailable.

## VPS Tailnet enrollment

Installing/authenticating Tailscale is privileged and therefore never occurs as
an implicit side effect of run, push, or pull.

Explicit enrollment is:

~~~bash
git shadow network ensure <host> tailscale
~~~

The command requires an auth key supplied through one of:

~~~text
GIT_SHADOW_TAILSCALE_AUTH_KEY
TS_AUTH_KEY
~~~

The auth key is sent over the existing SSH stdin stream into a temporary 0600
file and is not embedded in the remote shell command.

Automatic installation currently targets Linux VPS hosts and requires
passwordless sudo. Detection and access-path use are independent of this
installation helper.

## Diagnostics

Read-only diagnostics:

~~~bash
git shadow network status <host>
git shadow probe <host>
~~~

They report whether:

- Tailscale exists locally;
- Tailscale exists remotely;
- the remote node is a visible peer;
- the tailnet identifiers match;
- the observed peer path is direct / peer-relay / DERP.

## Failure law

Access-path failure is not host failure:

~~~text
CloudCLI Web unavailable != Git/Shadow host unavailable
~~~

If neither Tailscale Serve nor a configured public URL is available, git-shadow
continues the Git and Shadow lanes in sync-only mode.

## China / filtering boundary

Neither Cloudflare Tunnel nor Tailscale is modeled as a guaranteed mechanism for
bypassing national network filtering.

The Access Path layer records reachability and route quality only. It does not
claim that a provider endpoint, public hostname, control plane, relay, or
protocol will remain reachable from a particular jurisdiction.

Deployment-specific Internet reachability must be measured independently.

## Invariants

1. No personal CloudCLI domain may be hard-coded in runtime code.
2. CloudCLI stays localhost-bound by default.
3. Tailscale enrollment is explicit because it is privileged and changes node
   identity.
4. Tailscale Serve configuration may be automatic only after both nodes are
   already visible peers.
5. Cloudflare/public URL configuration remains optional.
6. A failed Web access path cannot disable Git/Shadow synchronization.
7. The final Session URL must use the same base URL selected by the Access Path
   resolver.
