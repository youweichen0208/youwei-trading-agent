import uvicorn
from youwei_runner.app import create_app
from youwei_runner.settings import RunnerSettings


def main():
    # Single process owns ephemeral receipts and container reconciliation.
    uvicorn.run(create_app(RunnerSettings()), host="0.0.0.0", port=8091, workers=1,
                limit_concurrency=8)
