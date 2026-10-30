"""Request models for the Wildstone integration (ADR 0026)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ConnectionUpdate(BaseModel):
    keystone_url: str = Field(description="Wildstone's Keystone address, e.g. https://wildstone.home.arpa")
    publisher_token: str | None = Field(
        default=None,
        description="The publisher token Wildstone minted for PantryOS. Omit to keep the current one.",
    )


class MemberLinkUpdate(BaseModel):
    member: str | None = Field(
        default=None, description="The Wildstone member this profile is. Empty or null removes the link."
    )
