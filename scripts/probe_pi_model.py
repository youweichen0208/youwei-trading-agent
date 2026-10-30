"""One real Anthropic-compatible request; private selected route arrives on stdin."""

import json
import sys

import anthropic


def main():
    route = json.load(sys.stdin)
    with anthropic.Anthropic(
        api_key=route["api_key"],
        base_url=route["base_url"],
        default_headers=route["headers"],
        max_retries=0,
        timeout=30,
    ) as client:
        response = client.messages.create(
            model=route["model"],
            max_tokens=64,
            messages=[
                {"role": "user", "content": "Reply with exactly YTA_MODEL_OK and nothing else."}
            ],
            extra_body={"thinking": {"type": "disabled"}},
        )
    content = "".join(block.text for block in response.content if block.type == "text")
    result = {
        "real_provider": True,
        "requested_model": route["model"],
        "response_model": response.model,
        "stop_reason": response.stop_reason,
        "marker_matches": content.strip() == "YTA_MODEL_OK",
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    print(json.dumps(result))
    if not result["marker_matches"]:
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            json.dumps(
                {
                    "error_type": type(error).__name__,
                    "http_status": getattr(error, "status_code", None),
                }
            )
        )
        raise SystemExit(1) from None
