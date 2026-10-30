"""Safe wire projection after complete registered signal and report replay."""

from yta_alpha.backtest_contracts import BacktestResponse
from yta_alpha.forecast import content_hash


def backtest_response(registry, identity, mode):
    payload = registry.get(identity, mode=mode)
    return BacktestResponse(
        id=identity,
        content_hash=content_hash(payload),
        input_hash=content_hash(payload["input"]),
        code_sha256=payload["code_sha256"],
        report=payload["report"],
    )
