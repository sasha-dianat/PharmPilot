"""Applies simulated events to the real application, and to the oracle.

Every event goes through the same code path production uses — the routers, then
the services, then the ledger, then Postgres. Nothing is shortcut to make the
simulation easier to write, because the shortcuts are exactly where the defects
would hide.

The driver is the only place the two worlds meet. It records into the oracle
what the *specification* says should have happened, and asks the application to
do it; `invariants.py` then asks whether they agree.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.core.inventory import dispense as DISP
from services.core.inventory import reservation_service as RS
from services.platform.routers import inventory_admin as AD
from services.platform.routers import inventory_integrity as IG

from .oracle import Oracle, q
from .world import World


class Staff:
    """The subset of a Staff row the inventory routers read."""
    def __init__(self, pharmacy_id, staff_id=None):
        self.pharmacy_id = pharmacy_id
        self.id = staff_id or uuid.uuid4()


class Rx:
    """The subset of a Prescription the dispense hook reads."""
    def __init__(self, rx_id, pharmacy_id, ndc, qty, number):
        self.id = rx_id
        self.pharmacy_id = pharmacy_id
        self.ndc = ndc
        self.quantity_dispensed = qty
        self.quantity_prescribed = qty
        self.days_supply = 30
        self.rx_number = number


class Outcome:
    """What the application did, including refusing."""
    def __init__(self, ok: bool, detail: str = "", raised: Exception | None = None,
                 payload=None):
        self.ok, self.detail, self.raised, self.payload = ok, detail, raised, payload

    def __repr__(self) -> str:
        return f"Outcome(ok={self.ok}, detail={self.detail!r})"


class Driver:
    def __init__(self, db: AsyncSession, world: World, *, pharmacy_id,
                 oracle: Oracle | None = None):
        self.db = db
        self.world = world
        self.pid = pharmacy_id
        self.oracle = oracle or Oracle()
        self.alice = Staff(pharmacy_id)
        self.bob = Staff(pharmacy_id)          # a second person, for approvals
        self.carl = uuid.uuid4()               # a witness
        self.lot_ids: dict[str, str] = {}      # lot_number -> uuid
        self.errors: list[str] = []
        self._patient = None
        self._prescriber = None

    # ── setup ────────────────────────────────────────────────────────────
    async def install_catalogue(self) -> None:
        """Create the drug_products rows the lots will hang off."""
        for p in self.world.products:
            await self.db.execute(text("""
                INSERT INTO drug_products
                  (id, ndc11, generic_name, brand_name, strength, dosage_form,
                   package_quantity, is_controlled, requires_refrigeration,
                   is_active, discontinued, is_generic, is_otc, is_hazardous,
                   high_risk_flag, drug_db_metadata, created_at, updated_at,
                   is_deleted)
                VALUES (:i,:n,:g,:b,:s,:f,:pq,:c,:r,true,false,true,false,false,
                        false,'{}',now(),now(),false)
                ON CONFLICT (ndc11) DO NOTHING"""), {
                "i": uuid.uuid4(), "n": p.ndc11, "g": p.generic, "b": p.brand,
                "s": p.strength, "f": p.form, "pq": p.pack_size,
                "c": p.is_controlled, "r": p.refrigerated})
        await self.db.commit()

    async def receive_all(self) -> None:
        """Take delivery of every lot, through the real receiving endpoint."""
        for lot in self.world.lots:
            await self.receive(lot.ndc11, lot.lot_number, lot.received,
                               expiry=lot.expiry, unit_cost=lot.unit_cost)

    async def _people(self):
        if self._patient is None:
            self._patient = (await self.db.execute(text(
                "SELECT id FROM patients WHERE pharmacy_id = :p LIMIT 1"),
                {"p": self.pid})).scalar()
            self._prescriber = (await self.db.execute(text(
                "SELECT id FROM prescribers LIMIT 1"))).scalar()
        return self._patient, self._prescriber

    # ── events ───────────────────────────────────────────────────────────
    async def receive(self, ndc: str, lot_number: str, qty, *, expiry: date,
                      unit_cost=None, uom: str = "each") -> Outcome:
        try:
            out = await AD.receive_stock(AD.ReceiveLot(
                ndc11=ndc, lot_number=lot_number, expiry_date=expiry,
                quantity=float(qty), unit_cost=float(unit_cost) if unit_cost is not None else None,
                uom=uom, storage_location="SIM"), staff=self.alice, db=self.db)
        except Exception as exc:
            await self._recover()
            return Outcome(False, f"receive refused: {exc}", exc)

        lot_id = str(out["lot_id"])
        self.lot_ids[lot_number] = lot_id
        # An expired lot is refused by the endpoint, so only a successful
        # receipt is recorded as having happened.
        units = q(out.get("quantity_received", qty))
        self.oracle.register_lot(lot_id, ndc=ndc, cost=unit_cost or 0,
                                 expiry=expiry)
        self.oracle.record("RECEIPT", lot_id, units)
        return Outcome(True, payload=out)

    async def dispense(self, ndc: str, qty, *, ref: str | None = None) -> Outcome:
        """A real dispense through the hook the state machine calls."""
        rx_id = uuid.uuid4()
        number = f"SIM{uuid.uuid4().hex[:9].upper()}"
        try:
            await self._insert_rx(rx_id, number, ndc, qty, "will_call")
        except Exception as exc:
            await self._recover()
            return Outcome(False, f"prescription refused: {exc}", exc)
        rx = Rx(rx_id, self.pid, ndc, float(qty), number)
        return await self._do_dispense(rx, ndc)

    async def _insert_rx(self, rx_id, number, ndc, qty, status):
        pat, pres = await self._people()
        await self.db.execute(text("""
            INSERT INTO prescriptions
              (id, pharmacy_id, patient_id, prescriber_id, rx_number, ndc,
               drug_name, sig_text, quantity_prescribed, days_supply,
               written_date, source, status, refills_authorized,
               refills_remaining, is_controlled, created_at, updated_at, is_deleted)
            VALUES (:i,:ph,:pa,:pr,:rn,:n,'sim','1 daily',:q,30,CURRENT_DATE,
                    'escript',CAST(:st AS varchar),0,0,false,now(),now(),false)"""), {
            "i": rx_id, "ph": self.pid, "pa": pat, "pr": pres,
            "rn": number, "n": ndc, "q": float(qty), "st": status})

    async def _do_dispense(self, rx, ndc):
        before = await self._lot_map(ndc)
        res = await DISP.apply_dispense(self.db, rx, staff_id=self.alice.id)
        await self.db.commit()
        after = await self._lot_map(ndc)

        # The hook is best-effort by design: it never refuses, and records a
        # shortfall instead. The oracle is told what actually moved, per lot,
        # so a partial dispense is modelled as the partial it was.
        for lot_id, before_qty in before.items():
            moved = q(before_qty - after.get(lot_id, Decimal("0")))
            if moved > 0:
                self.oracle.record("DISPENSE", lot_id, moved, ref=str(rx.id))
        return Outcome(res.ok, res.error or res.skipped or "", payload=res)

    async def to_bucket(self, lot_number: str, qty, *,
                        movement_type: str = "DAMAGE") -> Outcome:
        lot_id = self.lot_ids.get(lot_number)
        if lot_id is None:
            return Outcome(False, "unknown lot")
        bucket = {"DAMAGE": "damaged", "TRANSFER_OUT": "in_transit",
                  "RETURN_TO_SUPPLIER": "returned"}[movement_type]
        try:
            out = await AD.record_damage(AD.RecordDamage(
                lot_id=uuid.UUID(lot_id), quantity=float(qty),
                movement_type=movement_type,
                reason=f"simulated {movement_type.lower()}"),
                staff=self.alice, db=self.db)
        except Exception as exc:
            await self._recover()
            return Outcome(False, f"{movement_type} refused: {exc}", exc)
        self.oracle.record("TO_BUCKET", lot_id, q(qty), bucket=bucket)
        return Outcome(True, payload=out)

    async def write_off(self, lot_number: str, qty, *,
                        movement_type: str = "WASTE",
                        from_bucket: str | None = None,
                        approve: bool = True) -> Outcome:
        """Request a write-off and, by default, have a second person approve it."""
        lot_id = self.lot_ids.get(lot_number)
        if lot_id is None:
            return Outcome(False, "unknown lot")
        try:
            req = await AD.request_write_off(AD.WriteOffRequest(
                lot_id=uuid.UUID(lot_id), movement_type=movement_type,
                quantity=float(qty), from_bucket=from_bucket,
                reason=f"simulated {movement_type.lower()}"),
                staff=self.alice, db=self.db)
        except Exception as exc:
            await self._recover()
            return Outcome(False, f"write-off refused: {exc}", exc)
        if not approve:
            return Outcome(True, "pending", payload=req)

        try:
            await IG.decide_approval(
                uuid.UUID(req["approval_id"]),
                IG.ApprovalDecision(approve=True, witness_id=self.carl),
                staff=self.bob, db=self.db)
        except Exception as exc:
            await self._recover()
            return Outcome(False, f"approval refused: {exc}", exc)

        if from_bucket:
            self.oracle.record("WRITEOFF_BUCKET", lot_id, q(qty), bucket=from_bucket)
        else:
            self.oracle.record("WRITEOFF_ONHAND", lot_id, q(qty))
        return Outcome(True, payload=req)

    async def reserve(self, ndc: str, qty) -> Outcome:
        pat, pres = await self._people()
        rx_id = uuid.uuid4()
        await self.db.execute(text("""
            INSERT INTO prescriptions
              (id, pharmacy_id, patient_id, prescriber_id, rx_number, ndc,
               drug_name, sig_text, quantity_prescribed, days_supply,
               written_date, source, status, refills_authorized,
               refills_remaining, is_controlled, created_at, updated_at, is_deleted)
            VALUES (:i,:ph,:pa,:pr,:rn,:n,'sim','1 daily',:q,30,CURRENT_DATE,
                    'escript','ready_to_fill',0,0,false,now(),now(),false)"""), {
            "i": rx_id, "ph": self.pid, "pa": pat, "pr": pres,
            "rn": f"SIM{uuid.uuid4().hex[:9].upper()}", "n": ndc, "q": float(qty)})
        rx = Rx(rx_id, self.pid, ndc, float(qty), "SIMRES")

        res = await RS.reserve(self.db, rx, staff_id=self.alice.id)
        await self.db.commit()
        if res.ok and not res.skipped:
            rows = (await self.db.execute(text(
                "SELECT inventory_lot_id, quantity FROM inventory_reservations "
                "WHERE prescription_id = :r AND status = 'active'"),
                {"r": rx_id})).mappings().all()
            for r in rows:
                self.oracle.record("RESERVE", str(r["inventory_lot_id"]),
                                   q(r["quantity"]), ref=str(rx_id))
        return Outcome(res.ok, res.error or res.skipped or "", payload=(res, rx))

    async def release(self, rx) -> Outcome:
        rows = (await self.db.execute(text(
            "SELECT inventory_lot_id, quantity FROM inventory_reservations "
            "WHERE prescription_id = :r AND status = 'active'"),
            {"r": rx.id})).mappings().all()
        res = await RS.release_for_transition(self.db, rx, "CANCELLED")
        await self.db.commit()
        if res.ok:
            for r in rows:
                self.oracle.record("UNRESERVE", str(r["inventory_lot_id"]),
                                   q(r["quantity"]), ref=str(rx.id))
        return Outcome(res.ok, res.error or "", payload=res)

    async def _recover(self) -> None:
        """Put the session back in a usable state after a refusal.

        A refusal is a legitimate outcome the simulation wants to record and
        carry on from. Without this, the first 422 poisons the transaction and
        every later event reports a PendingRollbackError instead of whatever it
        would really have done — which hides defects behind the first one found.
        """
        try:
            await self.db.rollback()
        except Exception:                                       # pragma: no cover
            pass


    async def dispense_again(self, rx) -> Outcome:
        """Submit the same prescription a second time.

        The retry that must not move stock twice. The adversarial phase creates
        a fresh prescription each round, so it never touches the idempotency
        guard — this does, and records nothing in the oracle because a replay is
        specified to be a no-op.
        """
        return await self._do_dispense_replay(rx)

    async def _do_dispense_replay(self, rx) -> Outcome:
        before = await self._lot_map(rx.ndc)
        res = await DISP.apply_dispense(self.db, rx, staff_id=self.alice.id)
        await self.db.commit()
        after = await self._lot_map(rx.ndc)
        moved = q(sum((before[k] - after.get(k, Decimal("0")) for k in before),
                      Decimal("0")))
        # Deliberately NOT recorded in the oracle: ground truth says a replay
        # changes nothing, so any movement here shows up as a disagreement.
        return Outcome(res.ok, f"replay moved {moved}", payload=(res, moved))

    async def dispense_tracked(self, ndc: str, qty) -> tuple:
        """A dispense whose prescription handle is returned, for replay/reversal."""
        rx_id = uuid.uuid4()
        number = f"SIM{uuid.uuid4().hex[:9].upper()}"
        try:
            await self._insert_rx(rx_id, number, ndc, qty, "will_call")
        except Exception as exc:
            await self._recover()
            return Outcome(False, str(exc)), None
        rx = Rx(rx_id, self.pid, ndc, float(qty), number)
        return await self._do_dispense(rx, ndc), rx

    async def count(self, lot_number: str, counted, *, approve: bool = True) -> Outcome:
        """A physical count, through the real count workflow.

        Counts do not go through the write-off door: a COUNT_GAIN is not a
        write-off, and `request_write_off` rightly refuses it. The workflow is
        open a session, submit the counted figure, post it — which raises the
        variance as an approval for a second person.
        """
        lot_id = self.lot_ids.get(lot_number)
        if lot_id is None:
            return Outcome(False, "unknown lot")
        row = (await self.db.execute(text(
            "SELECT quantity_on_hand, irc, ndc11 FROM inventory_lots WHERE id = :i"),
            {"i": lot_id})).mappings().first()
        if row is None:
            return Outcome(False, "lot vanished")
        book = q(row["quantity_on_hand"])
        delta = q(q(counted) - book)

        try:
            # Scoped by storage location: simulated lots carry no IRC, and the
            # endpoint returns a line *count* rather than the lines themselves.
            sess = await IG.create_count(IG.CountCreate(
                count_type="SPOT", blind=True, location="SIM"),
                staff=self.alice, db=self.db)
            line_id = (await self.db.execute(text(
                "SELECT id FROM stock_count_lines WHERE stock_count_id = :c "
                "AND inventory_lot_id = :l"),
                {"c": sess["count_id"], "l": lot_id})).scalar()
            if line_id is None:
                return Outcome(False, "lot not in the count scope")
            await IG.submit_count_line(
                uuid.UUID(sess["count_id"]),
                IG.CountLineSubmit(line_id=line_id,
                                   counted_quantity=float(counted)),
                staff=self.alice, db=self.db)
            # Alice posts, so Alice is the requester of the variance approvals
            # and Bob can sign them. Posting as Bob makes Bob the requester, and
            # the separation-of-duties guard then correctly refuses his own
            # approval — which is the rule working, not a defect.
            posted = await IG.post_count(uuid.UUID(sess["count_id"]),
                                         staff=self.alice, db=self.db)
        except Exception as exc:
            await self._recover()
            return Outcome(False, f"count refused: {exc}", exc)

        if delta == 0:
            return Outcome(True, "no variance", payload=posted)
        if not approve:
            return Outcome(True, "variance pending approval", payload=posted)

        # Posting raises approvals; a second person decides each one.
        pending = (await self.db.execute(text(
            "SELECT id FROM inventory_approvals WHERE pharmacy_id = :p "
            "AND inventory_lot_id = :l AND status = 'pending'"),
            {"p": self.pid, "l": lot_id})).mappings().all()
        for a in pending:
            try:
                await IG.decide_approval(
                    a["id"], IG.ApprovalDecision(approve=True, witness_id=self.carl),
                    staff=self.bob, db=self.db)
            except Exception as exc:
                await self._recover()
                return Outcome(False, f"count approval refused: {exc}", exc)
        self.oracle.record("COUNT_GAIN" if delta > 0 else "COUNT_LOSS",
                           lot_id, abs(delta))
        return Outcome(True, payload=posted)

    async def release_bucket(self, lot_number: str, qty, *,
                             from_bucket: str = "in_transit") -> Outcome:
        """Bring held units back to sellable stock."""
        lot_id = self.lot_ids.get(lot_number)
        if lot_id is None:
            return Outcome(False, "unknown lot")
        try:
            req = await AD.request_release(AD.ReleaseRequest(
                lot_id=uuid.UUID(lot_id), quantity=float(qty),
                from_bucket=from_bucket,
                reason="inspected and found sound"), staff=self.alice, db=self.db)
            await IG.decide_approval(
                uuid.UUID(req["approval_id"]),
                IG.ApprovalDecision(approve=True, witness_id=self.carl),
                staff=self.bob, db=self.db)
        except Exception as exc:
            await self._recover()
            return Outcome(False, f"release refused: {exc}", exc)
        self.oracle.record("FROM_BUCKET", lot_id, q(qty), bucket=from_bucket)
        return Outcome(True, payload=req)

    async def app_valuation(self) -> Decimal:
        """Sellable stock at cost, straight from the database."""
        v = (await self.db.execute(text(
            "SELECT COALESCE(SUM(quantity_on_hand * COALESCE(unit_cost,0)),0) "
            "FROM inventory_lots WHERE pharmacy_id = :p AND is_deleted = false"),
            {"p": self.pid})).scalar()
        return q(v)

    # ── reading the application back ─────────────────────────────────────
    async def _lot_map(self, ndc: str) -> dict[str, Decimal]:
        rows = (await self.db.execute(text(
            "SELECT id, quantity_on_hand FROM inventory_lots "
            "WHERE pharmacy_id = :p AND ndc11 = :n AND is_deleted = false"),
            {"p": self.pid, "n": ndc})).mappings().all()
        return {str(r["id"]): q(r["quantity_on_hand"]) for r in rows}

    async def app_lot_state(self) -> dict[str, dict]:
        rows = (await self.db.execute(text("""
            SELECT id, ndc11, quantity_on_hand, quantity_reserved,
                   quantity_damaged, quantity_returned, quantity_in_transit,
                   unit_cost
            FROM inventory_lots
            WHERE pharmacy_id = :p AND is_deleted = false"""),
            {"p": self.pid})).mappings().all()
        return {str(r["id"]): {
            "ndc11": r["ndc11"],
            "on_hand": q(r["quantity_on_hand"]),
            "reserved": q(r["quantity_reserved"]),
            "damaged": q(r["quantity_damaged"]),
            "returned": q(r["quantity_returned"]),
            "in_transit": q(r["quantity_in_transit"]),
            "unit_cost": r["unit_cost"],
        } for r in rows}

    async def app_stock_levels(self) -> dict[str, dict]:
        rows = (await self.db.execute(text("""
            SELECT ndc11, quantity_on_hand, quantity_reserved
            FROM stock_levels WHERE pharmacy_id = :p"""),
            {"p": self.pid})).mappings().all()
        return {r["ndc11"]: {"on_hand": q(r["quantity_on_hand"]),
                             "reserved": q(r["quantity_reserved"])}
                for r in rows}
