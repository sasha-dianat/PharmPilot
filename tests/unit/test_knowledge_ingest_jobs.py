import asyncio
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.platform.routers import knowledge


class StaffStub:
    id = str(uuid4())
    pharmacy_id = str(uuid4())
    role = "pharmacist"

    def has_permission(self, _permission: str) -> bool:
        return True


class StubSession:
    def __init__(self):
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


class SuccessfulPipeline:
    def __init__(self, job_id: str):
        self.job_id = job_id

    async def ingest_pdf(self, **kwargs):
        assert knowledge.INGEST_JOBS[self.job_id]["status"] == "running"
        progress_callback = kwargs["progress_callback"]
        progress_callback({"pages_total": 3, "pages_done": 1})
        progress_callback({"pages_done": 3})
        return {"status": "complete", "chunks_stored": 12}


class FailingPipeline:
    async def ingest_file(self, **_kwargs):
        raise RuntimeError("embedding service unavailable")


def setup_function():
    knowledge.INGEST_JOBS.clear()


def test_ingest_job_transitions_to_completed_and_records_chunks(tmp_path):
    temp_file = tmp_path / "guide.pdf"
    temp_file.write_bytes(b"%PDF-1.4")
    job = knowledge._register_ingest_job("guide.pdf")
    session = StubSession()

    assert job["status"] == "queued"

    asyncio.run(knowledge._run_ingest_job(
        job_id=job["job_id"],
        ingest_method="ingest_pdf",
        temp_path=str(temp_file),
        ingest_kwargs={"pdf_path": str(temp_file)},
        pipeline_factory=lambda _db: SuccessfulPipeline(job["job_id"]),
        session_factory=lambda: session,
    ))

    record = knowledge.INGEST_JOBS[job["job_id"]]
    assert record["status"] == "completed"
    assert record["chunks_stored"] == 12
    assert record["pages_total"] == 3
    assert record["pages_done"] == 3
    assert record["error"] is None
    assert record["started_at"] is not None
    assert record["finished_at"] is not None
    assert session.committed is True
    assert temp_file.exists() is False


def test_ingest_job_failure_is_captured_and_does_not_raise(tmp_path):
    temp_file = tmp_path / "upload.txt"
    temp_file.write_text("reference", encoding="utf-8")
    job = knowledge._register_ingest_job("upload.txt")
    session = StubSession()

    asyncio.run(knowledge._run_ingest_job(
        job_id=job["job_id"],
        ingest_method="ingest_file",
        temp_path=str(temp_file),
        ingest_kwargs={"file_path": str(temp_file)},
        pipeline_factory=lambda _db: FailingPipeline(),
        session_factory=lambda: session,
    ))

    record = knowledge.INGEST_JOBS[job["job_id"]]
    assert record["status"] == "failed"
    assert "embedding service unavailable" in record["error"]
    assert record["finished_at"] is not None
    assert session.committed is False
    assert session.rolled_back is True
    assert temp_file.exists() is False


def _knowledge_client() -> TestClient:
    app = FastAPI()
    app.include_router(knowledge.router, prefix="/knowledge")

    async def override_staff():
        return StaffStub()

    app.dependency_overrides[knowledge.get_current_staff] = override_staff
    return TestClient(app)


def test_get_ingest_job_returns_record_and_404_for_unknown_id():
    job = knowledge._register_ingest_job("formulary.docx")
    knowledge._update_ingest_job(job["job_id"], status="completed", chunks_stored=4)
    client = _knowledge_client()

    response = client.get(f"/knowledge/ingest/jobs/{job['job_id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job["job_id"]
    assert body["status"] == "completed"
    assert body["chunks_stored"] == 4

    missing = client.get(f"/knowledge/ingest/jobs/{uuid4()}")
    assert missing.status_code == 404
