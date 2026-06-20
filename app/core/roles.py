"""
app/core/roles.py

All user roles in one place. When a new role is needed, add it here.
Every permission check in the codebase imports from here.
"""
from enum import Enum


class UserRole(str, Enum):
    USER = "user"               # Standard registered user
    ADMIN = "admin"             # Can manage users; extended visibility in logs
    SUPER_ADMIN = "super_admin" # Full system visibility; can trigger crash tests


class NotePermission(str, Enum):
    VIEW = "view"   # Can read the note
    EDIT = "edit"   # Can read and write the note


class InviteStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    EXPIRED = "expired"
