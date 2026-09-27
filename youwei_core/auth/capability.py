"""Compatibility exports for the shared capability wire contract."""
from youwei_contracts.capability import Capability, CapabilityError, sign_capability, verify_capability

__all__ = ["Capability", "CapabilityError", "sign_capability", "verify_capability"]
