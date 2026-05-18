---
name: beds24-auth
description: Beds24 v2 authentication — refresh token vs access token, invite-code bootstrap, and the "Token not valid" 401 trap that bites integrations (Make/n8n) when a refresh token is sent where an access token is expected. Use when setting up or debugging any Beds24 API integration, configuring connectors in automation platforms, or rotating credentials.
---

# Beds24 v2 Auth — at a glance

Two-token model: long-life **refresh token** mints short-life **access tokens**. Every API call other than `/authentication/token` requires an access token. Sending the refresh token as `token:` gets you `401 "Token not valid"`.

See [auth.md](./auth.md) for the full reference: invite-code bootstrap, the access-token fetch call, scope choices, Make/n8n pitfalls, and rotation.
