"""
Intelligence Services — the fourteen offline-first intelligent engines.

Each engine follows the same shape:
  • a LOCAL brain that always works (classical ML / stats / local rules), and
  • optional CLOUD enrichment wrapped in tier_resolver.cloud_call(),
returning the universal §1.2 envelope via intelligence_core.build_envelope().

Built so far:
  expiry_prevention  — #12 Inventory Expiry Waste Prevention
  supply_warning     — #17 Supply-Chain Disruption Early Warning
  margin_optimizer   — #19 Financial Intelligence & Margin Optimization
"""
