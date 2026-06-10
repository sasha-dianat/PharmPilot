"""
Shared SQLAlchemy declarative models — single registry for the whole platform.

WHY THIS FILE EAGERLY IMPORTS EVERY MODEL MODULE
=================================================
Several models reference each other through *string* forward references in
``relationship()`` (e.g. ``Prescription.patient: Mapped["Patient"] =
relationship(back_populates="prescriptions")``). SQLAlchemy resolves those
strings against its declarative class registry the first time mapper
configuration runs (``configure_mappers()``) — which is triggered lazily, by
whichever code path first touches a mapped class attribute (e.g.
``select(Prescription).where(Prescription.id == ...)``).

If that first trigger fires before every referenced class has been imported,
SQLAlchemy raises ``InvalidRequestError: ... failed to locate a name`` — and,
critically, **caches that failure on the mapper** (`_configure_failed`), so
*every subsequent* attempt to use ANY model in the registry raises the same
cached error for the lifetime of the process. This previously surfaced as two
seemingly-unrelated failures in ``tests/unit/test_sse_ticket_auth.py`` purely
because of *test collection order* — ``shared.models.prescription`` got
imported (registering ``Prescription``) before ``shared.models.patient`` ever
did, and the first mapper-configuration trigger poisoned the registry for the
rest of the run. The exact same hazard exists in production: whichever request
happens to be the first to touch a mapped attribute in a fresh worker process
determines whether the whole process's ORM layer comes up healthy.

The fix is the standard SQLAlchemy idiom (the same one Alembic's ``env.py``
relies on via ``target_metadata``): import every declarative model module here,
in ``shared/models/__init__.py``, so that simply doing ``import shared.models``
(or importing any single model from this package) guarantees the registry is
**fully populated before any mapper can possibly be configured**. Do not remove
any of these imports, and add new model modules here as they're created.
"""

from .base import AuditedBase, Base, TimestampedBase  # noqa: F401

# Import every model module so all mapped classes are registered on Base's
# declarative registry before SQLAlchemy can lazily trigger configure_mappers().
# Order does not matter for correctness (that's the whole point) — alphabetical
# for readability.
from . import audio       # noqa: F401  (AudioTranscript, ProfileEnrichmentAction, ...)
from . import auth        # noqa: F401  (Staff, StaffSession, ...)
from . import biometric   # noqa: F401  (BiometricIdentity, PharmacyVisit, SecurityEvent, ...)
from . import claims      # noqa: F401  (ClaimTransaction, ERA835Record, DIRFeeAdjustment, ...)
from . import insurance   # noqa: F401  (InsurancePlan, PatientInsurance, ...)
from . import inventory   # noqa: F401  (DrugProduct, InventoryLot, StockLevel, PurchaseOrder, ...)
from . import patient     # noqa: F401  (Patient, PatientAllergy, LabResult, ClinicalNote, ...)
from . import pharmacy    # noqa: F401  (Pharmacy, ...)
from . import prescriber  # noqa: F401  (Prescriber, ...)
from . import prescription  # noqa: F401  (Prescription, PrescriptionFill, DURAlert, RxStateEvent, LabelEvent, ...)
