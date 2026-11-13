import uvicorn

from app.config import get_settings


def main() -> None:
    uvicorn.run("panel.main:app", host="0.0.0.0", port=get_settings().web_port, log_level="info")


if __name__ == "__main__":
    main()
