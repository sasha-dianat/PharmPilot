"""
Long-Term Care (LTC) Pharmacy Module
======================================
Manages the unique workflows of LTC (nursing home) pharmacy practice:

  Cycle fills: Instead of individual 30-day prescriptions, LTC pharmacies
  fill medications in synchronized cycles (typically 14 or 30 days) for
  all residents of a facility simultaneously.

  Blister/bubble packs: Medications are packaged in unit-dose blister cards
  with date/time cells for each med pass (BID, TID, QID, etc.).

  Med pass schedule: The pharmacy generates a fill list for each nursing unit
  based on the facility's med pass schedule (7AM, 12PM, 5PM, 9PM is typical).

  MDS (Minimum Data Set) integration: LTC facilities are required to submit
  MDS assessments to CMS; the pharmacy system must integrate with the facility's
  POS (Point of Service) system for accurate resident medication reconciliation.
"""
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)


class PackagingType(str, Enum):
    BLISTER_CARD     = "blister_card"      # 14 or 30-day blister pack
    UNIT_DOSE_CUP    = "unit_dose_cup"     # Individual cups per dose
    ORAL_LIQUID      = "oral_liquid"       # Oral syringe unit doses
    MULTI_DOSE_VIAL  = "multi_dose_vial"   # Injectables
    PATCH            = "patch"             # Transdermal patches


class CycleLength(int, Enum):
    FOURTEEN_DAY = 14
    THIRTY_DAY   = 30


@dataclass
class FacilityResident:
    """A nursing home resident on LTC pharmacy service."""
    resident_id: UUID
    facility_id: UUID
    room_number: str
    first_name: str
    last_name: str
    date_of_birth: date
    admission_date: date
    physician_name: str
    physician_npi: str
    diagnoses: list[str] = field(default_factory=list)
    allergies: list[str] = field(default_factory=list)
    diet_code: str = "regular"
    dnr_status: bool = False
    is_active: bool = True


@dataclass
class CycleFillOrder:
    """
    A cycle fill order — all medications for one resident for one cycle period.
    Generated automatically at the beginning of each facility billing cycle.
    """
    order_id: UUID = field(default_factory=uuid4)
    resident_id: UUID = field(default_factory=uuid4)
    facility_id: UUID = field(default_factory=uuid4)
    pharmacy_id: UUID = field(default_factory=uuid4)

    cycle_start: date = field(default_factory=date.today)
    cycle_end: Optional[date] = None
    cycle_length: CycleLength = CycleLength.THIRTY_DAY

    # Medications to fill this cycle
    line_items: list[dict] = field(default_factory=list)
    # [{rx_number, drug_name, ndc, sig, packaging_type, qty_per_cycle, days_supply}]

    status: str = "pending"  # pending | filled | delivered | billed

    # Med pass schedule (facility-specific)
    med_passes: list[str] = field(default_factory=lambda: ["07:00", "12:00", "17:00", "21:00"])

    # Delivery
    delivery_date: Optional[date] = None
    delivery_confirmed_by: Optional[str] = None

    def __post_init__(self):
        if not self.cycle_end:
            self.cycle_end = self.cycle_start + timedelta(days=self.cycle_length.value - 1)

    def calculate_quantities(self) -> None:
        """Calculate the quantity to fill per medication for the full cycle."""
        for item in self.line_items:
            sig = item.get("sig", "").lower()
            daily_doses = self._parse_daily_frequency(sig)
            cycle_days  = self.cycle_length.value
            item["qty_per_cycle"]  = daily_doses * cycle_days
            item["doses_per_day"]  = daily_doses
            item["days_supply"]    = cycle_days

    def _parse_daily_frequency(self, sig: str) -> int:
        """Parse SIG to determine doses per day."""
        if "qd" in sig or "daily" in sig or "once" in sig: return 1
        if "bid" in sig or "twice" in sig:                  return 2
        if "tid" in sig or "three" in sig:                  return 3
        if "qid" in sig or "four" in sig:                   return 4
        if "q4" in sig or "every 4" in sig:                 return 6
        if "q6" in sig or "every 6" in sig:                 return 4
        if "q8" in sig or "every 8" in sig:                 return 3
        if "q12" in sig or "every 12" in sig:               return 2
        if "prn" in sig or "as needed" in sig:              return 1
        return 1


@dataclass
class BlisterPackLabel:
    """
    Label content for a blister/bubble pack card.
    Regulatory label requirements vary by state — configurable per deployment.
    """
    resident_name: str
    room_number: str
    facility_name: str
    drug_name: str
    strength: str
    sig_text: str
    pharmacy_name: str
    pharmacy_phone: str
    pharmacist_name: str
    rx_number: str
    prescriber_name: str
    cycle_dates: str          # "Jan 1 – Jan 30, 2025"
    refills: str
    lot_number: str
    discard_after: str        # Beyond-use date
    # Med pass cells (date × time grid)
    cell_layout: list[list[str]] = field(default_factory=list)
    # 30 rows (days) × 4 cols (med passes) — cell content is dose or empty


class CycleFillEngine:
    """
    Generates cycle fill orders for all residents of a LTC facility.
    """

    def __init__(self, db=None):
        self.db = db

    async def generate_facility_cycle(
        self,
        facility_id: UUID,
        pharmacy_id: UUID,
        cycle_start: date,
        cycle_length: CycleLength = CycleLength.THIRTY_DAY,
    ) -> list[CycleFillOrder]:
        """
        Generate cycle fill orders for ALL active residents of a facility.
        Called at the start of each billing cycle.
        """
        if not self.db:
            return []

        from sqlalchemy import text

        # Load all active residents
        residents_result = await self.db.execute(text("""
            SELECT id, room_number, first_name, last_name
            FROM facility_residents
            WHERE facility_id = :fac_id AND is_active = true
            ORDER BY room_number
        """), {"fac_id": str(facility_id)})

        orders = []
        for resident in residents_result.mappings().all():
            # Load active prescriptions for this resident
            rx_result = await self.db.execute(text("""
                SELECT pr.rx_number, pr.drug_name, pr.ndc, pr.sig_text,
                       pr.quantity_prescribed, pr.days_supply, pr.refills_remaining
                FROM prescriptions pr
                WHERE pr.patient_id = :patient_id
                  AND pr.status NOT IN ('dispensed','cancelled','transferred_out')
                  AND pr.expiry_date >= :cycle_start
                ORDER BY pr.drug_name
            """), {
                "patient_id": str(resident["id"]),
                "cycle_start": cycle_start,
            })

            line_items = [
                {
                    "rx_number":   row["rx_number"],
                    "drug_name":   row["drug_name"],
                    "ndc":         row["ndc"],
                    "sig":         row["sig_text"],
                    "packaging":   PackagingType.BLISTER_CARD.value,
                }
                for row in rx_result.mappings().all()
            ]

            order = CycleFillOrder(
                resident_id=resident["id"],
                facility_id=facility_id,
                pharmacy_id=pharmacy_id,
                cycle_start=cycle_start,
                cycle_length=cycle_length,
                line_items=line_items,
            )
            order.calculate_quantities()
            orders.append(order)

        logger.info(
            "Cycle fill generated for facility %s: %d residents, cycle %s – %s",
            str(facility_id)[:8], len(orders), cycle_start, cycle_start + timedelta(days=cycle_length.value)
        )
        return orders

    def generate_blister_pack_label(
        self,
        order: CycleFillOrder,
        line_item: dict,
        resident: FacilityResident,
        facility_name: str,
        pharmacy_name: str,
        pharmacy_phone: str,
        pharmacist_name: str,
    ) -> BlisterPackLabel:
        """Generate a blister pack label for a single medication."""
        cycle_dates = (
            f"{order.cycle_start.strftime('%b %d')} – "
            f"{order.cycle_end.strftime('%b %d, %Y') if order.cycle_end else ''}"
        )

        # Build cell grid: 30 rows × med passes per day
        daily_doses = line_item.get("doses_per_day", 1)
        cell_layout = []
        for day in range(order.cycle_length.value):
            row = []
            for pass_time in order.med_passes:
                # Fill cells based on frequency
                row.append("●" if len(row) < daily_doses else "")
            cell_layout.append(row)

        return BlisterPackLabel(
            resident_name=f"{resident.last_name}, {resident.first_name}",
            room_number=resident.room_number,
            facility_name=facility_name,
            drug_name=line_item.get("drug_name", ""),
            strength="",  # Extracted from drug_name
            sig_text=line_item.get("sig", ""),
            pharmacy_name=pharmacy_name,
            pharmacy_phone=pharmacy_phone,
            pharmacist_name=pharmacist_name,
            rx_number=line_item.get("rx_number", ""),
            prescriber_name=resident.physician_name,
            cycle_dates=cycle_dates,
            refills=str(line_item.get("refills_remaining", 0)),
            lot_number="",  # Filled at dispensing
            discard_after=(order.cycle_end.strftime("%m/%d/%Y") if order.cycle_end else ""),
            cell_layout=cell_layout,
        )


class MedPassScheduleGenerator:
    """
    Generates the medication administration schedule for nursing staff.
    Output: a structured schedule showing which medications go to which
    residents at each med pass time.
    """

    def generate_pass_schedule(
        self,
        orders: list[CycleFillOrder],
        residents_by_id: dict[UUID, FacilityResident],
        pass_date: date,
        pass_time: str,
    ) -> list[dict]:
        """
        Returns a list of medication administration records for one med pass.
        Sorted by room number for nursing staff convenience.
        """
        pass_list = []
        for order in orders:
            resident = residents_by_id.get(order.resident_id)
            if not resident:
                continue

            pass_index = order.med_passes.index(pass_time) if pass_time in order.med_passes else -1
            if pass_index < 0:
                continue

            for item in order.line_items:
                daily_doses = item.get("doses_per_day", 1)
                if pass_index < daily_doses:
                    pass_list.append({
                        "room": resident.room_number,
                        "resident_name": f"{resident.last_name}, {resident.first_name}",
                        "drug_name": item.get("drug_name", ""),
                        "dose": item.get("sig", ""),
                        "rx_number": item.get("rx_number", ""),
                        "administration_time": pass_time,
                        "date": pass_date.isoformat(),
                    })

        return sorted(pass_list, key=lambda x: x["room"])
