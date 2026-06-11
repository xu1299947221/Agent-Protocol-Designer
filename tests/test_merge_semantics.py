from protocol_designer.core import merge_protocol


def test_preserve_structural_fields_and_strip_meta_prefixes():
    old = {
        "operations": [
            {
                "name": "set_field_value",
                "description": "从用户话语中提取字段设置或字段纠正意图，并写入会话字段值。",
                "llm_role": "理解用户自然语言中的字段值。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "field_id": {"type": "string"},
                        "value": {"type": "string"},
                    },
                    "required": ["session_id", "field_id", "value"],
                },
            }
        ]
    }
    new = {
        "operations": [
            {
                "name": "set_field_value",
                "description": "差异：大纲已确认后写入字段时，必须使当前大纲失效。",
                "llm_role": "无变化：理解用户自然语言中的字段值。",
                "input_schema": {"type": "object", "properties": {}, "required": []},
            }
        ]
    }
    merged = merge_protocol(old, new)
    op = merged["operations"][0]
    assert not op["description"].startswith("差异：")
    assert not op["llm_role"].startswith("无变化：")
    assert op["input_schema"]["properties"]["session_id"]["type"] == "string"
    assert op["input_schema"]["required"] == ["session_id", "field_id", "value"]


def test_rule_dedupe_with_meta_prefixes():
    old = {
        "confirmation_rules": [
            "新增：大纲已确认后变更字段，必须告知用户此修改会让大纲失效。",
            "删除章节数量大于等于根级章节数量的百分之八十时，必须二次确认。",
        ],
        "clarification_rules": [
            "新增：章节级操作包含全文级范围词但目标只覆盖局部章节，必须追问要改这一章还是整篇。"
        ],
    }
    new = {
        "confirmation_rules": [
            "大纲已确认后变更字段，必须告知用户此修改会让大纲失效。",
            "调整：删除章节数量大于等于根级章节数量的百分之八十时，必须二次确认。",
        ],
        "clarification_rules": [
            "章节级操作包含全文级范围词但目标只覆盖局部章节，必须追问要改这一章还是整篇。"
        ],
    }
    merged = merge_protocol(old, new)
    assert len(merged["confirmation_rules"]) == 2
    assert len(merged["clarification_rules"]) == 1
    assert all(not rule.startswith(("新增：", "调整：")) for rule in merged["confirmation_rules"])
    assert all(not rule.startswith("新增：") for rule in merged["clarification_rules"])
