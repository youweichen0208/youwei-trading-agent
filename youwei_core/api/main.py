"""youwei-api entrypoint."""

import uvicorn

from youwei_core.api.app import create_app
from youwei_core.config import Settings


def main() -> None:
    settings = Settings()
    uvicorn.run(
        create_app(settings),
        host="127.0.0.1",
        port=8000,
        log_level="info",
    )


if __name__ == "__main__":
    main()
