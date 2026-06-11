from protocol_designer.agent_registry import (
    add_agent_version,
    get_agent_version,
    list_agents,
    load_agent,
    register_agent,
    session_from_agent,
)


def _session(name="写作 Agent", op="draft_doc"):
    return {
        "session_id": "session_1",
        "title": name,
        "protocol": {
            "project_name": name,
            "domain_summary": "帮助用户生成文档",
            "operations": [{"name": op, "description": "生成文档"}],
            "workflow": {"nodes": [{"id": "draft", "type": "agent", "operation": op}]},
        },
    }


def test_register_agent_and_list_summary(tmp_path):
    agent = register_agent(tmp_path, _session(), name="写作助手", description="文档生成")
    agents = list_agents(tmp_path)
    loaded = load_agent(tmp_path, agent["agent_id"])

    assert agent["current_version"] == "v1"
    assert agents[0]["name"] == "写作助手"
    assert agents[0]["version_count"] == 1
    assert loaded["versions"][0]["summary"]["operation_count"] == 1


def test_add_agent_version_and_get_current_version(tmp_path):
    agent = register_agent(tmp_path, _session(), name="写作助手")
    updated = add_agent_version(tmp_path, agent["agent_id"], _session(op="review_doc"), notes="补审校")
    current = get_agent_version(updated)

    assert updated["current_version"] == "v2"
    assert len(updated["versions"]) == 2
    assert current["version_id"] == "v2"
    assert current["notes"] == "补审校"


def test_clone_agent_version_to_session(tmp_path):
    agent = register_agent(tmp_path, _session(), name="写作助手")
    cloned = session_from_agent(agent, session_id="new_session")

    assert cloned["session_id"] == "new_session"
    assert cloned["protocol"]["project_name"] == "写作 Agent"
    assert cloned["registry_source"]["agent_id"] == agent["agent_id"]
    assert cloned["history"]
