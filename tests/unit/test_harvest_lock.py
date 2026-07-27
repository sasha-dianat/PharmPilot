"""One crawl at a time across NFI + دارونامه jobs — the Iran proxy is a system
proxy, so parallel harvests are both impolite and broken."""
import pytest

from services.core.drug_catalog import harvest_lock


@pytest.fixture(autouse=True)
def _clean_lock():
    harvest_lock.force_release()
    yield
    harvest_lock.force_release()


def test_acquire_release_roundtrip():
    assert harvest_lock.holder() is None
    assert harvest_lock.acquire("nfi") is True
    assert harvest_lock.holder() == "nfi"
    harvest_lock.release("nfi")
    assert harvest_lock.holder() is None


def test_second_acquire_blocked_and_reports_holder():
    assert harvest_lock.acquire("nfi")
    assert harvest_lock.acquire("coverage:tamin") is False
    assert harvest_lock.holder() == "nfi"


def test_release_by_non_holder_is_ignored():
    assert harvest_lock.acquire("coverage:tamin")
    harvest_lock.release("nfi")
    assert harvest_lock.holder() == "coverage:tamin"


def test_nfi_service_reports_lock_holder_in_status():
    from services.core.drug_catalog import nfi_harvest_service as svc
    assert harvest_lock.acquire("coverage:tamin")
    snap = svc.status()
    assert snap["lock_holder"] == "coverage:tamin"


def test_nfi_start_refuses_while_lock_held():
    from services.core.drug_catalog import nfi_harvest_service as svc
    assert harvest_lock.acquire("coverage:tamin")
    with pytest.raises(RuntimeError):
        svc.start(1, 2)
