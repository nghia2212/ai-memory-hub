"""Synthetic STDIO JSON-RPC 2.0 MCP server fixture for V3.1 Gateway integration tests (NOT a production integration)."""
import json
import sys
import time


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "v1"
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        msg = json.loads(line)
        method = msg.get("method")
        req_id = msg.get("id")
        if method == "notifications/initialized":
            continue
        if method == "initialize":
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "fixture-stdio-mcp", "version": mode},
                    "capabilities": {"tools": {}},
                },
            }
        elif method == "tools/list":
            if mode == "v2_schema_change":
                tools = [
                    {
                        "name": "echo_tool",
                        "description": "Echo input text (v2 changed schema)",
                        "inputSchema": {
                            "type": "object",
                            "required": ["text", "extra_required"],
                            "properties": {"text": {"type": "string"}, "extra_required": {"type": "string"}},
                        },
                    }
                ]
            else:
                tools = [
                    {
                        "name": "echo_tool",
                        "description": "Echo input text",
                        "inputSchema": {
                            "type": "object",
                            "required": ["text"],
                            "properties": {"text": {"type": "string"}, "sleep_sec": {"type": "number"}, "repeat": {"type": "integer"}},
                        },
                    },
                    {
                        "name": "temp_tool",
                        "description": "Tool removed in v2",
                        "inputSchema": {"type": "object", "properties": {}},
                    },
                ]
            resp = {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}}
        elif method == "tools/call":
            params = msg.get("params") or {}
            tname = params.get("name")
            args = params.get("arguments") or {}
            if tname == "echo_tool":
                if args.get("sleep_sec"):
                    time.sleep(float(args["sleep_sec"]))
                rep = max(1, int(args.get("repeat", 1)))
                txt = str(args.get("text", "")) * rep
                resp = {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": txt}]}}
            else:
                resp = {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Unknown tool {tname}"}}
        else:
            resp = {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "Method not found"}}
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
