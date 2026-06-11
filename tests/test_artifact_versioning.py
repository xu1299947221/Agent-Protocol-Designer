from protocol_designer.workflow_runtime import rollback_artifact_in_job, run_workflow_once, runtime_result_to_job


def test_runtime_artifacts_have_versions_snapshots_and_trace_source():
    protocol = {
        "workflow": {
            "nodes": [
                {"id": "draft_v1", "type": "agent", "name": "生成初稿", "outputs": ["draft_document"]},
                {"id": "draft_v2", "type": "agent", "name": "改写初稿", "outputs": ["draft_document"], "depends_on": ["draft_v1"]},
            ],
        },
    }

    result = run_workflow_once(protocol=protocol, user_message="生成并改写文档")
    artifacts = result["artifacts"]
    versions = result["artifact_versions"]

    assert artifacts[0]["version"] == "v1"
    assert artifacts[1]["version"] == "v2"
    assert artifacts[0]["is_active"] is False
    assert artifacts[1]["is_active"] is True
    assert artifacts[1]["previous_version_id"] == artifacts[0]["id"]
    assert artifacts[1]["snapshot_id"]
    assert artifacts[1]["source_trace_id"]
    assert versions["rollback_points"][0]["can_rollback_to"] == artifacts[0]["id"]
    assert result["summary"]["rollback_point_count"] == 1


def test_runtime_artifact_rollback_marks_target_active():
    protocol = {
        "workflow": {
            "nodes": [
                {"id": "draft_v1", "type": "agent", "name": "生成初稿", "outputs": ["draft_document"]},
                {"id": "draft_v2", "type": "agent", "name": "改写初稿", "outputs": ["draft_document"], "depends_on": ["draft_v1"]},
            ],
        },
    }
    result = run_workflow_once(protocol=protocol, user_message="生成并改写文档")
    job = runtime_result_to_job(job_id="job_version", session_id="session_1", result=result)
    target_id = result["artifacts"][0]["id"]

    updated = rollback_artifact_in_job(job, target_id)
    artifacts = updated["result"]["artifacts"]

    assert next(item for item in artifacts if item["id"] == target_id)["is_active"] is True
    assert any(item.get("step") == "artifact_rollback" for item in updated["result"]["trace"])
