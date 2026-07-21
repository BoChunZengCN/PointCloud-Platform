from fastapi.testclient import TestClient

from pc_system.api import create_app
from phase14_helpers import write_completed_run


HEADERS = {"X-API-Key": "secret"}


def create_session(client):
    return client.post(
        "/segmentation-corrections/scan",
        headers=HEADERS,
        json={
            "run_id": "run-001",
            "session_id": "session-001",
            "sample_id": "sample-001",
            "actor": "alice",
        },
    )


def test_api_correction_read_write_flow(tmp_path):
    write_completed_run(tmp_path)
    client = TestClient(create_app(tmp_path, api_key="secret"))

    created = create_session(client)
    event = client.post(
        "/segmentation-corrections/scan/session-001/events",
        headers=HEADERS,
        json={
            "actor": "alice",
            "expected_revision": 0,
            "client_request_id": "request-1",
            "operation": {
                "type": "relabel",
                "instance_ids": ["obj-001"],
                "class_id": "pipe",
            },
        },
    )
    sessions = client.get("/segmentation-corrections/scan")
    detail = client.get("/segmentation-corrections/scan/session-001")
    points = client.get(
        "/segmentation-corrections/scan/session-001/points?offset=0&limit=10"
    )
    objects = client.get("/segmentation-corrections/scan/session-001/objects")
    queue = client.get("/segmentation-corrections/scan/session-001/queue")
    events = client.get("/segmentation-corrections/scan/session-001/events")

    assert created.status_code == 201
    assert event.status_code == 200
    assert event.json()["revision"] == 1
    assert sessions.json()["session_count"] == 1
    assert detail.json()["session_id"] == "session-001"
    assert points.json()["total"] == 4
    assert objects.json()["object_count"] == 2
    assert queue.json()["item_count"] == 2
    assert events.json()["event_count"] == 1


def test_api_submit_publish_and_read_release(tmp_path):
    write_completed_run(tmp_path)
    client = TestClient(create_app(tmp_path, api_key="secret"))
    assert create_session(client).status_code == 201
    submitted = client.post(
        "/segmentation-corrections/scan/session-001/submit",
        headers=HEADERS,
        json={"actor": "alice", "expected_revision": 0},
    )
    published = client.post(
        "/segmentation-corrections/scan/session-001/publish",
        headers=HEADERS,
        json={
            "release_id": "release-001",
            "reviewer": "bob",
            "expected_revision": 1,
            "benchmark_split": "development",
            "license": "internal",
        },
    )

    assert submitted.status_code == 200
    assert submitted.json()["status"] == "in_review"
    assert published.status_code == 201
    assert published.json()["status"] == "published"
    assert client.get("/segmentation-correction-releases/scan").json()[
        "release_count"
    ] == 1
    assert client.get(
        "/segmentation-correction-releases/scan/release-001"
    ).json()["release_id"] == "release-001"


def test_all_correction_writes_require_api_key(tmp_path):
    write_completed_run(tmp_path)
    client = TestClient(create_app(tmp_path, api_key="secret"))

    assert client.post("/segmentation-corrections/scan", json={}).status_code == 401
    assert (
        client.post(
            "/segmentation-corrections/scan/session-001/events", json={}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/segmentation-corrections/scan/session-001/submit", json={}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/segmentation-corrections/scan/session-001/publish", json={}
        ).status_code
        == 401
    )


def test_api_maps_stale_revision_lock_and_invalid_identifier(tmp_path):
    write_completed_run(tmp_path)
    client = TestClient(create_app(tmp_path, api_key="secret"))
    assert create_session(client).status_code == 201
    stale = client.post(
        "/segmentation-corrections/scan/session-001/events",
        headers=HEADERS,
        json={
            "actor": "alice",
            "expected_revision": 9,
            "client_request_id": "request-stale",
            "operation": {"type": "confirm", "instance_ids": ["obj-001"]},
        },
    )
    locked = client.post(
        "/segmentation-corrections/scan/session-001/events",
        headers=HEADERS,
        json={
            "actor": "bob",
            "expected_revision": 0,
            "client_request_id": "request-bob",
            "operation": {"type": "confirm", "instance_ids": ["obj-001"]},
        },
    )

    assert stale.status_code == 409
    assert locked.status_code == 423
    assert client.get("/segmentation-corrections/bad$id").status_code == 400


def test_authorized_invalid_payloads_and_paths_return_400_not_500(tmp_path):
    client = TestClient(
        create_app(tmp_path, api_key="secret"),
        raise_server_exceptions=False,
    )

    invalid_create_path = client.post(
        "/segmentation-corrections/bad$id", headers=HEADERS, json={}
    )
    missing_create_fields = client.post(
        "/segmentation-corrections/scan", headers=HEADERS, json={}
    )
    invalid_queue_path = client.get(
        "/segmentation-corrections/bad$id/session-001/queue"
    )

    assert invalid_create_path.status_code == 400
    assert missing_create_fields.status_code == 400
    assert invalid_queue_path.status_code == 400
