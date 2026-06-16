"""Inventory movement rules — pure functions for manual stock changes
(adjustments, returns, damage/expiry write-offs, recall removals). No I/O, so
the validation and quantity math are unit-testable and live outside the router.

The persisted audit row is `shared.models.inventory.InventoryMovement`.
"""
from __future__ import annotations

# Movement types and whether they SET an absolute count or apply a signed delta.
# ADJUSTMENT/CORRECTION set on-hand to an absolute new count; the rest remove units.
MOVEMENT_TYPES = {
    "ADJUSTMENT",      # manual count correction → absolute new quantity
    "CORRECTION",      # fix a prior erroneous movement → absolute new quantity
    "RETURN",          # send units back to wholesaler / patient return → removal
    "DAMAGE",          # broken/contaminated write-off → removal
    "EXPIRY_REMOVAL",  # pull expired stock → removal
    "RECALL_REMOVAL",  # pull recalled lot → removal
}
ABSOLUTE_TYPES = {"ADJUSTMENT", "CORRECTION"}
REMOVAL_TYPES = MOVEMENT_TYPES - ABSOLUTE_TYPES


class MovementError(ValueError):
    """Raised when a movement is invalid (bad type, negative qty, oversell)."""


def validate_type(movement_type: str) -> None:
    if movement_type not in MOVEMENT_TYPES:
        raise MovementError(f"unknown movement_type: {movement_type!r}")


def compute_absolute(before: float, new_quantity: float) -> dict:
    """Adjust on-hand to an absolute new count. Delta may be + or -."""
    before = float(before)
    new_quantity = float(new_quantity)
    if new_quantity < 0:
        raise MovementError("new_quantity cannot be negative")
    return {
        "quantity_before": before,
        "quantity_after": new_quantity,
        "quantity_delta": round(new_quantity - before, 3),
    }


def compute_removal(before: float, quantity: float) -> dict:
    """Remove `quantity` units. Cannot remove more than on hand or a non-positive
    amount — those are operator errors, not silent clamps."""
    before = float(before)
    quantity = float(quantity)
    if quantity <= 0:
        raise MovementError("removal quantity must be positive")
    if quantity > before:
        raise MovementError(
            f"cannot remove {quantity} — only {before} on hand"
        )
    return {
        "quantity_before": before,
        "quantity_after": round(before - quantity, 3),
        "quantity_delta": round(-quantity, 3),
    }


def plan_movement(*, movement_type: str, before: float,
                  new_quantity: float | None = None,
                  quantity: float | None = None) -> dict:
    """Resolve a movement to before/after/delta based on its type.

    Absolute types (ADJUSTMENT/CORRECTION) take `new_quantity`; removal types
    (RETURN/DAMAGE/EXPIRY_REMOVAL/RECALL_REMOVAL) take `quantity`.
    """
    validate_type(movement_type)
    if movement_type in ABSOLUTE_TYPES:
        if new_quantity is None:
            raise MovementError(f"{movement_type} requires new_quantity")
        return compute_absolute(before, new_quantity)
    if quantity is None:
        raise MovementError(f"{movement_type} requires quantity")
    return compute_removal(before, quantity)
