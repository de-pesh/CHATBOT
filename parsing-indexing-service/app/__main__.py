import logging

import uvicorn
from app.api_server import app
from app.config import get_settings

if __name__ == "__main__":
    settings = get_settings()
    # The one place the server's logging is configured.
    logging.basicConfig(level=settings.LOG_LEVEL.upper(), format='%(asctime)s - %(levelname)s - %(message)s')
    uvicorn.run(app, host=settings.HOST, port=settings.PORT, log_config=None)
