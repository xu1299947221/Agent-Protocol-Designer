from webui_server import list_runtime_jobs, load_runtime_job, save_runtime_job


def test_runtime_job_file_storage_roundtrip(tmp_path, monkeypatch):
    import webui_server

    monkeypatch.setattr(webui_server, "RUNTIME_DIR", tmp_path)
    job = {
        "job_id": "job_1",
        "session_id": "session_1",
        "title": "测试 Runtime Job",
        "status": "awaiting_human",
        "summary": {"total_nodes": 2, "completed_nodes": 1},
        "waiting_for": {"node_id": "confirm"},
        "created_at": "2026-06-05T00:00:00",
        "updated_at": "2026-06-05T00:00:01",
        "result": {"status": "awaiting_human"},
    }

    save_runtime_job(job)
    loaded = load_runtime_job("job_1")
    jobs = list_runtime_jobs("session_1")

    assert loaded["job_id"] == "job_1"
    assert jobs[0]["job_id"] == "job_1"
    assert jobs[0]["waiting_for"]["node_id"] == "confirm"
