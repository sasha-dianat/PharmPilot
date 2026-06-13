"""
Medication Package Visual Verification — Phase 24
===================================================
Camera-based ML system that verifies the *visual appearance* of medication
packaging at the point of dispensing — before the pharmacist review step.

Workflow:
  1. ENROLLMENT (at receiving / stock-in):
       Technician takes 5–10 photos of the new product's packaging
       (sachet, blister, bottle, box, vial, etc.) from multiple angles.
       The engine extracts visual feature embeddings and stores a per-NDC
       reference profile in a local SQLite + numpy index.

  2. VERIFICATION (at dispensing):
       As items are pulled from the shelf to fill a prescription, a camera
       captures the packaging. The engine compares the live embedding to the
       enrolled reference for the expected NDC.
         • Match ≥ threshold → ✅  pass — hand to pharmacist for clinical check
         • Match < threshold → ❌  fail — wrong drug, wrong strength, or wrong
                               manufacturer brand; alert issued before pharmacist sees it

Architecture:
  PackageFeatureExtractor   — MobileNetV3 embeddings (torch/torchvision), with
                              histogram + HOG fallback when torch not available
  PackageEnrollmentEngine   — multi-photo enrollment → mean reference embedding
  PackageVerificationEngine — live-frame matching against enrolled index
  PackageIndex              — SQLite metadata + .npy embedding storage (~/.pharmpilot/)

All inference runs locally. No internet required. No cloud API called.
"""
from .feature_extractor   import PackageFeatureExtractor
from .enrollment_engine   import PackageEnrollmentEngine, EnrolledProduct
from .verification_engine import PackageVerificationEngine, VerificationResult

__all__ = [
    "PackageFeatureExtractor",
    "PackageEnrollmentEngine",
    "EnrolledProduct",
    "PackageVerificationEngine",
    "VerificationResult",
]
