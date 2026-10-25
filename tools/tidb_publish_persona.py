#!/usr/bin/env python3
"""把就医通人设文档发布到专用 Agent，并创建冻结该配置的新会话。"""
import argparse
import hashlib
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path


def load_api_class():
    source = Path(__file__).with_name("tidb_text_stability.py")
    spec = importlib.util.spec_from_file_location("tidb_text_stability", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Api


def extract_prompt(document):
    text = Path(document).read_text(encoding="utf-8")
    start = text.index("```text\n") + len("```text\n")
    end = text.index("\n```", start)
    return text[start:end]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--document", required=True)
    parser.add_argument("--sdkconfig", default="D:/Agent_link/firmware/build/config/sdkconfig.json")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    prompt = extract_prompt(args.document)
    prompt_bytes = len(prompt.encode("utf-8"))
    prompt_crlf_bytes = len(prompt.replace("\n", "\r\n").encode("utf-8"))
    if max(prompt_bytes, prompt_crlf_bytes) >= 4096:
        raise RuntimeError("人设正文 LF/CRLF 至少一种达到或超过 4096 UTF-8 字节，拒绝发布")

    api = load_api_class()(args.sdkconfig)
    before = api.request("GET", "/api/v1/user/agents/" + api.agent_id)
    before_agent = before.get("agent", before)
    backup_path = Path(args.out).with_name(Path(args.out).stem + "_backup.json")
    backup_path.write_text(json.dumps({
        "agentId": api.agent_id,
        "additionalInstructions": before_agent.get("additionalInstructions", ""),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    api.request("PATCH", "/api/v1/user/agents/" + api.agent_id,
                {"additionalInstructions": prompt})
    after = api.request("GET", "/api/v1/user/agents/" + api.agent_id)
    readback = after.get("agent", after).get("additionalInstructions", "")
    if readback != prompt:
        raise RuntimeError("发布后回读不一致，已保留发布前备份：%s" % backup_path)

    session_id = api.create_session("persona-publish")
    record = {
        "publishedAt": datetime.now(timezone.utc).isoformat(),
        "agentId": api.agent_id,
        "sessionId": session_id,
        "prompt_chars": len(prompt),
        "prompt_bytes": prompt_bytes,
        "prompt_crlf_bytes": prompt_crlf_bytes,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "readback_equal": True,
        "backup": str(backup_path),
    }
    Path(args.out).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
