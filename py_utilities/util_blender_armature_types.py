"""Common armature type definitions."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class BoneSpec:
    """Specification for a single bone to be created in an armature.

    Attributes:
        name:        Bone name.
        parent_name: Name of the parent bone, or ``None`` for a root bone.
    """
    name: str
    parent_name: Optional[str] = None
